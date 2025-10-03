# app/pages/students.py
from __future__ import annotations
import datetime as dt
import json, ast
from typing import Any, Dict, Iterable, Tuple, List, Optional
from dateutil import parser as dateparser
from flask import request, jsonify, flash, url_for
from sqlalchemy import text

from ..core import render, get_db, get_customers_db
from ..accounting import ensure_company


# -----------------------
# Helpers
# -----------------------
def _parse_payload(val: Any) -> Dict[str, Any]:
    """Return dict from jsonb/str payload robustly."""
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            try:
                return ast.literal_eval(val)
            except Exception:
                return {}
    return {}


def _safe_now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _as_aware(ts: Any) -> Optional[dt.datetime]:
    if ts is None:
        return None

    if isinstance(ts, str):
        candidate = ts.strip()
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            ts = dt.datetime.fromisoformat(candidate)
        except ValueError:
            try:
                ts = dateparser.isoparse(candidate)
            except (ValueError, TypeError):
                return None

    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)

    return None


def _lesson_maps_from_structure(struct: Any) -> Dict[str, Dict[str, Any]]:
    """
    Build {lesson_uid: {title, section_title, order, section_order}} from structure json/jsonb.
    """
    if isinstance(struct, str):
        try:
            struct = json.loads(struct)
        except Exception:
            try:
                struct = ast.literal_eval(struct)
            except Exception:
                return {}
    if not isinstance(struct, dict):
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for sec in (struct.get("sections") or []):
        sec_title = sec.get("title") or ""
        sec_order = sec.get("order")
        for les in (sec.get("lessons") or []):
            uid = les.get("lesson_uid")
            if not uid:
                continue
            out[str(uid)] = {
                "title": les.get("title") or str(uid),
                "section_title": sec_title,
                "order": les.get("order"),
                "section_order": sec_order,
            }
    return out


def _load_course_maps(cdb, course_ids: Iterable[int]) -> Tuple[Dict[int, str], Dict[int, Dict[str, Dict[str, Any]]]]:
    """
    Return (course_title_map, course_lesson_map):
      - course_title_map: {course_id: title}
      - course_lesson_map: {course_id: {lesson_uid: {title, section_title, ...}}}
    """
    course_ids = sorted({int(c) for c in course_ids if c is not None})
    title_map: Dict[int, str] = {}
    lesson_map: Dict[int, Dict[str, Dict[str, Any]]] = {}

    if not course_ids:
        return title_map, lesson_map

    for cid in course_ids:
        row = cdb.execute(
            text("SELECT id, title, structure FROM courses WHERE id = :cid"),
            {"cid": cid}
        ).mappings().first()
        if not row:
            continue
        title_map[cid] = row.get("title") or f"Course {cid}"
        lesson_map[cid] = _lesson_maps_from_structure(row.get("structure"))
    return title_map, lesson_map


# -----------------------
# Routes
# -----------------------
def register(app):
    @app.route("/students")
    def students():
        """
        If course_id is missing: show a picker of courses with learner counts.
        If course_id is present: show ONLY learners who have activity in that course (ever).
        """
        db = get_db()
        company = ensure_company(db)

        cdb = get_customers_db()
        course_id = request.args.get("course_id", type=int)

        # --- No course selected: show course list with counts ---
        if not course_id:
            # Courses with activity, plus published courses (in case of zero activity yet)
            rows = cdb.execute(text("""
                SELECT
                  c.id,
                  c.title,
                  c.is_published,
                  c.published_at,
                  (SELECT COUNT(DISTINCT al.user_id) FROM activity_log al WHERE al.course_id = c.id) AS learners,
                  (SELECT MAX(al.created_at)            FROM activity_log al WHERE al.course_id = c.id) AS last_activity
                FROM courses c
                WHERE
                  c.is_published = TRUE
                  OR EXISTS (SELECT 1 FROM activity_log z WHERE z.course_id = c.id)
                ORDER BY c.id
            """)).mappings().all()

            courses: List[Dict[str, Any]] = []
            for r in rows:
                courses.append({
                    "id": r["id"],
                    "title": r.get("title") or f"Course {r['id']}",
                    "is_published": bool(r.get("is_published")),
                    "published_at": r.get("published_at"),
                    "learners": int(r.get("learners") or 0),
                    "last_activity": _as_aware(r.get("last_activity")) if r.get("last_activity") else None,
                    "url": url_for("students", course_id=r["id"]),
                })

            return render("students.html", company=company, course_id=None, courses=courses)

        # --- Course selected: show learners with latest activity in that course ---
        # Latest row per learner for the selected course (all-time)
        latest_sql = text("""
            WITH latest AS (
              SELECT DISTINCT ON (al.user_id)
                     al.user_id,
                     al.lesson_uid,
                     al.a_type,
                     al.created_at AS last_seen,
                     al.payload
                FROM activity_log al
               WHERE al.course_id = :cid
            ORDER BY al.user_id, al.created_at DESC
            )
            SELECT l.user_id, l.lesson_uid, l.a_type, l.last_seen, l.payload,
                   r.user_email, r.first_name, r.middle_name, r.last_name, r.invoice_name
              FROM latest l
              LEFT JOIN registrations r
                     ON r.id = l.user_id
          ORDER BY l.last_seen DESC NULLS LAST, l.user_id
        """)
        rows = cdb.execute(latest_sql, {"cid": course_id}).mappings().all()

        # Load course title + lesson map for this course
        course_title_map, course_lesson_map = _load_course_maps(cdb, [course_id])
        lesson_map = course_lesson_map.get(course_id, {})
        course_title = course_title_map.get(course_id, f"Course {course_id}")

        items = []
        for r in rows:
            lesson_uid = r.get("lesson_uid")
            info = lesson_map.get(str(lesson_uid) if lesson_uid is not None else "", {})
            lesson_title = info.get("title") or (lesson_uid or "")

            name = " ".join([p for p in [r.get("first_name"), r.get("middle_name"), r.get("last_name")] if p]) \
                   or r.get("invoice_name") \
                   or r.get("user_email") \
                   or f"User #{r['user_id']}"

            payload = _parse_payload(r.get("payload"))
            kind = payload.get("kind") or r.get("a_type") or "view"
            detail = ""
            if payload.get("kind") == "exam":
                ev = payload.get("event") or ""
                prog = payload.get("progress_percent")
                detail = f"exam:{ev}"
                if prog is not None:
                    try:
                        detail += f" • {float(prog):.0f}%"
                    except Exception:
                        pass
            elif payload.get("kind") == "unlock":
                to_ = payload.get("to"); frm = payload.get("from")
                detail = f"unlock {frm}→{to_}" if (to_ is not None or frm is not None) else "unlock"

            last_seen = _as_aware(r.get("last_seen")) if r.get("last_seen") else None

            items.append({
                "user_id": r["user_id"],
                "name": name,
                "email": r.get("user_email"),
                "course_id": course_id,
                "course_title": course_title,
                "lesson_uid": lesson_uid,
                "lesson_title": lesson_title,
                "last_seen": last_seen,
                "kind": kind,
                "detail": detail,
                "detail_url": url_for("student_detail", user_id=r["user_id"], course_id=course_id),
            })

        return render("students.html",
                      company=company,
                      course_id=course_id,
                      course_title=course_title,
                      items=items)

    @app.route("/students.json")
    def students_json():
        """
        If course_id missing: return list of courses with learner counts.
        Else: return learners for the course with their latest activity.
        """
        cdb = get_customers_db()
        course_id = request.args.get("course_id", type=int)

        if not course_id:
            rows = cdb.execute(text("""
                SELECT
                  c.id,
                  c.title,
                  c.is_published,
                  c.published_at,
                  (SELECT COUNT(DISTINCT al.user_id) FROM activity_log al WHERE al.course_id = c.id) AS learners,
                  (SELECT MAX(al.created_at)            FROM activity_log al WHERE al.course_id = c.id) AS last_activity
                FROM courses c
                WHERE
                  c.is_published = TRUE
                  OR EXISTS (SELECT 1 FROM activity_log z WHERE z.course_id = c.id)
                ORDER BY c.id
            """)).mappings().all()
            courses = []
            for r in rows:
                courses.append({
                    "id": r["id"],
                    "title": r.get("title"),
                    "is_published": bool(r.get("is_published")),
                    "published_at": (r.get("published_at").isoformat() if r.get("published_at") else None),
                    "learners": int(r.get("learners") or 0),
                    "last_activity": (r.get("last_activity").isoformat() if r.get("last_activity") else None),
                })
            return jsonify({"courses": courses})

        latest_sql = text("""
            WITH latest AS (
              SELECT DISTINCT ON (al.user_id)
                     al.user_id,
                     al.lesson_uid,
                     al.a_type,
                     al.created_at AS last_seen,
                     al.payload
                FROM activity_log al
               WHERE al.course_id = :cid
            ORDER BY al.user_id, al.created_at DESC
            )
            SELECT l.user_id, l.lesson_uid, l.a_type, l.last_seen, l.payload,
                   r.user_email, r.first_name, r.middle_name, r.last_name, r.invoice_name
              FROM latest l
              LEFT JOIN registrations r
                     ON r.id = l.user_id
          ORDER BY l.last_seen DESC NULLS LAST, l.user_id
        """)
        rows = cdb.execute(latest_sql, {"cid": course_id}).mappings().all()

        course_title_map, course_lesson_map = _load_course_maps(cdb, [course_id])
        lesson_map = course_lesson_map.get(course_id, {})

        items = []
        for r in rows:
            uid = r.get("lesson_uid")
            info = lesson_map.get(str(uid) if uid is not None else "", {})
            lesson_title = info.get("title") or (uid or "")

            name = " ".join([p for p in [r.get("first_name"), r.get("middle_name"), r.get("last_name")] if p]) \
                   or r.get("invoice_name") \
                   or r.get("user_email") \
                   or f"User #{r['user_id']}"

            payload = _parse_payload(r.get("payload"))
            kind = payload.get("kind") or r.get("a_type") or "view"

            items.append({
                "user_id": r["user_id"],
                "name": name,
                "email": r.get("user_email"),
                "lesson_uid": uid,
                "lesson_title": lesson_title,
                "last_seen": (r.get("last_seen").isoformat() if r.get("last_seen") else None),
                "kind": kind,
                "payload": payload,
            })

        return jsonify({"course_id": course_id, "items": items})

    @app.route("/students/<int:user_id>/<int:course_id>")
    def student_detail(user_id: int, course_id: int):
        """
        Drill-down timeline for a single student in a single course.
        """
        db = get_db()
        company = ensure_company(db)
        cdb = get_customers_db()

        # Student info (by user_id only)
        reg = cdb.execute(
            text("""
                SELECT id AS user_id, user_email,
                       first_name, middle_name, last_name,
                       invoice_name, enrollment_status
                  FROM registrations
                 WHERE id = :uid
                 LIMIT 1
            """),
            {"uid": user_id}
        ).mappings().first()

        # Course title + lesson map
        course_title_map, course_lesson_map = _load_course_maps(cdb, [course_id])
        lesson_map = course_lesson_map.get(course_id, {})
        course_title = course_title_map.get(course_id, f"Course {course_id}")

        # Events in this course (all-time; sort desc)
        events = cdb.execute(
            text("""
                SELECT id, user_id, course_id, lesson_uid, a_type, created_at, payload
                  FROM activity_log
                 WHERE user_id = :uid
                   AND course_id = :cid
              ORDER BY created_at DESC
                 LIMIT 2000
            """),
            {"uid": user_id, "cid": course_id}
        ).mappings().all()

        def _mk_event(ev):
            payload = _parse_payload(ev.get("payload"))
            uid = ev.get("lesson_uid")
            info = lesson_map.get(str(uid) if uid is not None else "", {})
            lesson_title = info.get("title") or (uid or "")
            section_title = info.get("section_title") or ""

            kind = payload.get("kind") or ev.get("a_type") or "view"
            event = payload.get("event")
            progress = payload.get("progress_percent")
            extra = ""
            if kind == "exam":
                parts = [p for p in ["exam", event] if p]
                if progress is not None:
                    try:
                        parts.append(f"{float(progress):.0f}%")
                    except Exception:
                        pass
                extra = " • ".join(parts)
            elif kind == "unlock":
                to_ = payload.get("to"); frm = payload.get("from")
                extra = f"unlock {frm}→{to_}" if (to_ is not None or frm is not None) else "unlock"

            created = _as_aware(ev.get("created_at")) if ev.get("created_at") else None

            return {
                "id": ev.get("id"),
                "created_at": created,
                "lesson_uid": uid,
                "lesson_title": lesson_title,
                "section_title": section_title,
                "a_type": ev.get("a_type"),
                "kind": kind,
                "event": event,
                "progress_percent": progress,
                "summary": extra,
                "raw_payload": payload,
            }

        timeline = [_mk_event(e) for e in events]

        # Student display name
        if reg:
            name = " ".join([p for p in [reg.get("first_name"), reg.get("middle_name"), reg.get("last_name")] if p]) \
                   or reg.get("invoice_name") \
                   or reg.get("user_email") \
                   or f"User #{user_id}"
            email = reg.get("user_email")
            enrollment_status = reg.get("enrollment_status")
        else:
            name = f"User #{user_id}"
            email = None
            enrollment_status = None

        return render(
            "students_detail.html",
            company=company,
            user_id=user_id,
            course_id=course_id,
            course_title=course_title,
            name=name,
            email=email,
            enrollment_status=enrollment_status,
            timeline=timeline,
        )
