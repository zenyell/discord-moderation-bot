"""panel_admins.py — DB helpers for panel admin accounts."""
import database as db

# Available permissions a panel admin can be granted
ALL_PERMISSIONS = [
    "view_dashboard",
    "view_audit_log",
    "manage_moderation",
    "manage_settings",
    "manage_logging",
    "manage_commands",
    "manage_reaction_roles",
    "manage_autoroles",
    "manage_tags",
    "manage_triggers",
    "manage_suggestions",
    "view_user_profiles",
]

PERM_LABELS = {
    "view_dashboard":        "View Dashboard",
    "view_audit_log":        "View Audit Log",
    "manage_moderation":     "Manage Moderation",
    "manage_settings":       "Manage Settings",
    "manage_logging":        "Manage Logging",
    "manage_commands":       "Manage Commands",
    "manage_reaction_roles": "Manage Reaction Roles",
    "manage_autoroles":      "Manage Autoroles",
    "manage_tags":           "Manage Tags",
    "manage_triggers":       "Manage Triggers",
    "manage_suggestions":    "Manage Suggestions",
    "view_user_profiles":    "View User Profiles",
}


async def _init_table():
    await db._exec("""
        CREATE TABLE IF NOT EXISTS panel_admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id TEXT NOT NULL UNIQUE,
            label TEXT DEFAULT '',
            permissions TEXT DEFAULT '',
            added_at TEXT DEFAULT (datetime('now'))
        )
    """)


def init_table_sync():
    db._sync(_init_table())


# ── CRUD ──────────────────────────────────────────────────────────────────

async def _add_admin(discord_id: str, label: str, permissions: list) -> bool:
    perms_str = ",".join(p for p in permissions if p in ALL_PERMISSIONS)
    try:
        await db._exec(
            "INSERT OR IGNORE INTO panel_admins (discord_id, label, permissions) VALUES (?,?,?)",
            (discord_id.strip(), label.strip(), perms_str)
        )
        return True
    except Exception:
        return False


async def _update_admin(discord_id: str, label: str, permissions: list):
    perms_str = ",".join(p for p in permissions if p in ALL_PERMISSIONS)
    await db._exec(
        "UPDATE panel_admins SET label=?, permissions=? WHERE discord_id=?",
        (label.strip(), perms_str, discord_id.strip())
    )


async def _remove_admin(discord_id: str):
    await db._exec("DELETE FROM panel_admins WHERE discord_id=?", (discord_id.strip(),))


async def _get_all() -> list:
    rs = await db._exec("SELECT * FROM panel_admins ORDER BY added_at DESC")
    rows = db._rows(rs)
    for r in rows:
        r["permissions_list"] = [p for p in r.get("permissions", "").split(",") if p]
    return rows


async def _get_admin(discord_id: str) -> dict:
    rs = await db._exec("SELECT * FROM panel_admins WHERE discord_id=?", (discord_id.strip(),))
    row = db._one(rs)
    if row:
        row["permissions_list"] = [p for p in row.get("permissions", "").split(",") if p]
    return row


async def _has_permission(discord_id: str, perm: str) -> bool:
    row = await _get_admin(discord_id)
    if not row:
        return False
    return perm in row["permissions_list"]


# sync wrappers
def add_admin_sync(discord_id, label, permissions):  return db._sync(_add_admin(discord_id, label, permissions))
def update_admin_sync(discord_id, label, permissions): db._sync(_update_admin(discord_id, label, permissions))
def remove_admin_sync(discord_id):                    db._sync(_remove_admin(discord_id))
def get_all_sync():                                   return db._sync(_get_all())
def get_admin_sync(discord_id):                       return db._sync(_get_admin(discord_id))
def has_permission_sync(discord_id, perm):            return db._sync(_has_permission(discord_id, perm))
def is_panel_admin_sync(discord_id):                  return get_admin_sync(discord_id) is not None
