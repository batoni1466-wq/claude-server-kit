#!/usr/bin/env python3
# Patch CloudCLI (@cloudcli-ai/cloudcli 1.36.0) into a per-user jailed multi-user
# panel. Each web user "<username>" is confined to JAIL=<WORKSPACES_ROOT>/<username>
# (set WORKSPACES_ROOT=/srv/cloudcli-users in the server env). The jail is created with
# mode 0700, so no other account on the machine can read a person's files. Server-side
# enforcement at every projectId / path / cwd input point. Idempotent, keeps .orig backups,
# fails loudly if an anchor is missing.
# Usage: python3 patch-multiuser.py [INSTALL_DIR]
#   default INSTALL_DIR = /usr/lib/node_modules/@cloudcli-ai/cloudcli
import os, sys

BASE = sys.argv[1] if len(sys.argv) > 1 else '/usr/lib/node_modules/@cloudcli-ai/cloudcli'
SRV = os.path.join(BASE, 'dist-server', 'server')
report = []
modified = []


def patch(rel, repls):
    """Apply (old, new, expect[, mark]) replacements to <SRV>/<rel>.

    mark is the proof that a replacement is already in place. It defaults to the whole new
    text, which is exact but brittle: the day we edit our own replacement, a panel patched
    by the earlier version matches neither the upstream anchor nor the new text, and the
    installer aborts on every already-running server. Where a replacement is expected to
    keep evolving, pass a short line of ours that stays the same across versions.
    """
    path = os.path.join(SRV, rel)
    if not os.path.exists(path):
        raise SystemExit(f"FAIL: file missing: {path}")
    with open(path, encoding='utf-8') as f:
        s = f.read()
    orig = s
    report.append(f"== {rel}")
    for repl in repls:
        old, new, expect = repl[0], repl[1], repl[2]
        mark = repl[3] if len(repl) > 3 else new
        # Idempotency: if our text is already there, skip.
        if mark and mark in s:
            report.append(f"  skip (already applied): {old[:50]!r}")
            continue
        c = s.count(old)
        if c == 0:
            if new == '':
                report.append(f"  skip (removal already done): {old[:50]!r}")
                continue
            raise SystemExit(f"FAIL: anchor not found in {rel}: {old[:90]!r}")
        if expect is not None and c != expect:
            raise SystemExit(f"FAIL: anchor found {c}x, expected {expect}, in {rel}: {old[:70]!r}")
        s = s.replace(old, new)
        report.append(f"  ok {c}x: {old[:50]!r}")
    if s != orig:
        bak = path + '.orig'
        if not os.path.exists(bak):
            open(bak, 'w', encoding='utf-8').write(orig)
            report.append(f"  backup -> {os.path.basename(bak)}")
        open(path, 'w', encoding='utf-8').write(s)
        report.append(f"  WROTE {rel}")
        modified.append(rel)
    else:
        report.append(f"  no change {rel}")


def upgrade(rel, old, new):
    """Replace text that an EARLIER version of this patcher wrote.

    patch() anchors on pristine upstream code. As soon as one of our own replacements
    changes, a panel patched by the previous version holds neither the upstream anchor nor
    the new text, and patch() would abort - so re-running install.sh after such an edit
    would fail on every already-running server. This lifts that one piece to the current
    text first. No-op on a fresh package and on an already current file; loud on anything
    unexpected. Call it before patch() on the same file.
    """
    path = os.path.join(SRV, rel)
    if not os.path.exists(path):
        raise SystemExit(f"FAIL: file missing: {path}")
    with open(path, encoding='utf-8') as f:
        s = f.read()
    c = s.count(old)
    if c == 0:
        return
    if c != 1:
        raise SystemExit(f"FAIL: upgrade anchor found {c}x, expected 1, in {rel}: {old[:70]!r}")
    open(path, 'w', encoding='utf-8').write(s.replace(old, new))
    report.append(f"== {rel}")
    report.append(f"  upgraded from an earlier patch version: {old.strip()[:60]!r}")
    modified.append(rel)


# =====================================================================
# 1) shared/utils.js  --  inject the single shared jail primitive
# =====================================================================
UTILS_OLD = "export const WORKSPACES_ROOT = process.env.WORKSPACES_ROOT || os.homedir();"
UTILS_NEW = r"""export const WORKSPACES_ROOT = process.env.WORKSPACES_ROOT || os.homedir();
// ---------------------------
//----------------- PER-USER JAIL UTILITIES ------------
const JAIL_USERNAME_PATTERN = /^[a-zA-Z0-9_-]{1,64}$/;
/**
 * Computes the absolute jail root for a web user: <WORKSPACES_ROOT>/<username>.
 * Returns null for a missing/unsafe username so every caller fails closed.
 */
export function computeUserJail(username) {
    if (typeof username !== 'string' || !JAIL_USERNAME_PATTERN.test(username)) {
        return null;
    }
    const __legacyUsers = (process.env.JAIL_LEGACY_USERS || '').split(',').map(u => u.trim()).filter(Boolean);
    if (__legacyUsers.includes(username)) {
        try { return normalizeProjectPath(fs.realpathSync(os.homedir())); }
        catch { return normalizeProjectPath(os.homedir()); }
    }
    let base = path.resolve(WORKSPACES_ROOT);
    try {
        base = fs.realpathSync(base);
    }
    catch {
        // Base may not exist yet on first run; fall back to the resolved path.
    }
    return normalizeProjectPath(path.join(base, username));
}
/**
 * Resolves a candidate path against the jail, following symlinks on every
 * existing ancestor and re-appending any non-existent tail so a symlinked
 * parent cannot smuggle the target outside the jail. Fails closed on any error.
 */
export async function resolveInsideJail(candidatePath, jail) {
    if (!jail || typeof candidatePath !== 'string' || candidatePath.trim() === '') {
        return { inside: false, resolved: null };
    }
    try {
        const absolutePath = path.resolve(candidatePath);
        let existing = absolutePath;
        const missingSegments = [];
        for (;;) {
            try {
                existing = await realpath(existing);
                break;
            }
            catch (error) {
                if (error.code !== 'ENOENT') {
                    return { inside: false, resolved: null };
                }
                const parent = path.dirname(existing);
                if (parent === existing) {
                    existing = absolutePath;
                    missingSegments.length = 0;
                    break;
                }
                missingSegments.unshift(path.basename(existing));
                existing = parent;
            }
        }
        const resolved = normalizeProjectPath(missingSegments.length > 0
            ? path.join(existing, ...missingSegments)
            : existing);
        const inside = resolved === jail || resolved.startsWith(`${jail}${path.sep}`);
        return { inside, resolved: inside ? resolved : null };
    }
    catch {
        return { inside: false, resolved: null };
    }
}
/** Boolean convenience wrapper. */
export async function isPathInsideJail(candidatePath, jail) {
    return (await resolveInsideJail(candidatePath, jail)).inside;
}"""

patch('shared/utils.js', [
    (UTILS_OLD, UTILS_NEW, 1),
])

# =====================================================================
# 2) index.js  --  utils import + project-file jail middleware +
#                  browse-filesystem + create-folder
# =====================================================================
IDX_IMPORT_OLD = "import { AppError, WORKSPACES_ROOT, getOpenCodeDatabasePath, validateWorkspacePath } from './shared/utils.js';"
IDX_IMPORT_NEW = "import { AppError, WORKSPACES_ROOT, computeUserJail, getOpenCodeDatabasePath, isPathInsideJail, validateWorkspacePath } from './shared/utils.js';"

IDX_GUARD_OLD = r"""// Read file content endpoint
app.get('/api/projects/:projectId/file', authenticateToken, async (req, res) => {"""
IDX_GUARD_NEW = r"""// Per-user JAIL guard covering every projectId-scoped filesystem route below.
// Registered before the route handlers so it runs first; resolves the projectId
// to its real cwd and 404s unless that cwd is inside the caller's jail.
app.use([
    '/api/projects/:projectId/file',
    '/api/projects/:projectId/files',
    '/api/projects/:projectId/sessions/:sessionId/token-usage',
], authenticateToken, async (req, res, next) => {
    try {
        const jail = computeUserJail(req.user?.username);
        if (!jail) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        const projectRoot = await projectsDb.getProjectPathById(req.params.projectId);
        if (!projectRoot) {
            return res.status(404).json({ error: 'Project not found' });
        }
        if (!(await isPathInsideJail(projectRoot, jail))) {
            return res.status(404).json({ error: 'Project not found' });
        }
        next();
    }
    catch {
        return res.status(403).json({ error: 'Forbidden' });
    }
});
// Read file content endpoint
app.get('/api/projects/:projectId/file', authenticateToken, async (req, res) => {"""

IDX_BROWSE_OLD = r"""        // Default to home directory if no path provided
        const defaultRoot = WORKSPACES_ROOT;
        let targetPath = dirPath ? expandWorkspacePath(dirPath) : defaultRoot;
        // Resolve and normalize the path
        targetPath = path.resolve(targetPath);
        // Security check - ensure path is within allowed workspace root
        const validation = await validateWorkspacePath(targetPath);
        if (!validation.valid) {
            return res.status(403).json({ error: validation.error });
        }
        const resolvedPath = validation.resolvedPath || targetPath;"""
IDX_BROWSE_NEW = r"""        // Per-user jail: default to the caller's own jail and confine browsing to it.
        const jail = computeUserJail(req.user?.username);
        if (!jail) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        const defaultRoot = jail;
        let targetPath = dirPath ? expandWorkspacePath(dirPath) : defaultRoot;
        // Resolve and normalize the path
        targetPath = path.resolve(targetPath);
        // Security check - ensure path is within allowed workspace root
        const validation = await validateWorkspacePath(targetPath);
        if (!validation.valid) {
            return res.status(403).json({ error: validation.error });
        }
        const resolvedPath = validation.resolvedPath || targetPath;
        if (!(await isPathInsideJail(resolvedPath, jail))) {
            return res.status(403).json({ error: 'Forbidden' });
        }"""

IDX_MKDIR_OLD = r"""        const expandedPath = expandWorkspacePath(folderPath);
        const resolvedInput = path.resolve(expandedPath);
        const validation = await validateWorkspacePath(resolvedInput);
        if (!validation.valid) {
            return res.status(403).json({ error: validation.error });
        }
        const targetPath = validation.resolvedPath || resolvedInput;"""
IDX_MKDIR_NEW = r"""        const expandedPath = expandWorkspacePath(folderPath);
        const resolvedInput = path.resolve(expandedPath);
        const validation = await validateWorkspacePath(resolvedInput);
        if (!validation.valid) {
            return res.status(403).json({ error: validation.error });
        }
        const targetPath = validation.resolvedPath || resolvedInput;
        const jail = computeUserJail(req.user?.username);
        if (!jail || !(await isPathInsideJail(targetPath, jail))) {
            return res.status(403).json({ error: 'Forbidden' });
        }"""

patch('index.js', [
    (IDX_IMPORT_OLD, IDX_IMPORT_NEW, 1),
    (IDX_GUARD_OLD, IDX_GUARD_NEW, 1),
    (IDX_BROWSE_OLD, IDX_BROWSE_NEW, 1),
    (IDX_MKDIR_OLD, IDX_MKDIR_NEW, 1),
])

# =====================================================================
# 3) routes/auth.js  --  allow N users + create a private JAIL + optional display name
# =====================================================================
# The one place where a user's folder is born. It is created private, so nothing on the
# server that runs under another account can read it. Kept as its own pair of constants
# because upgrade() replays it on a panel patched by an earlier version of this file.
JAIL_MKDIR_OLD = """            // Create the user's JAIL directory (idempotent) so they have a home to work in.
            fs.mkdirSync(jail, { recursive: true });
"""
JAIL_MKDIR_NEW = """            // Create the user's JAIL directory (idempotent) so they have a home to work in.
            // 0700 from the first second: no other account on this machine (a bot, another
            // service) may read what this person works on. chmod repeats the mode because
            // mkdirSync leaves an already existing folder alone - which is what happens on a
            // password reset, where the row is deleted and the login registered again.
            fs.mkdirSync(jail, { recursive: true, mode: 0o700 });
            fs.chmodSync(jail, 0o700);
"""

# Two one-line inserts rather than one block of imports: each anchor is a single upstream
# line that never moves, and each carries its own mark. A file where something else has
# already added an import of its own (the server-side hardening puts jwt in here) then still
# patches instead of aborting.
AUTH_FS_OLD = "import express from 'express';\n"
AUTH_FS_NEW = "import express from 'express';\nimport fs from 'node:fs';\n"
AUTH_FS_MARK = "import fs from 'node:fs';"

AUTH_JAIL_IMPORT_OLD = "import { userDb } from '../modules/database/index.js';\n"
AUTH_JAIL_IMPORT_NEW = ("import { userDb } from '../modules/database/index.js';\n"
                        "import { computeUserJail } from '../shared/utils.js';\n")
AUTH_JAIL_IMPORT_MARK = "import { computeUserJail } from '../shared/utils.js';"

AUTH_REG_OLD = r"""        const { username, password } = req.body;
        // Validate input
        if (!username || !password) {
            return res.status(400).json({ error: 'Username and password are required' });
        }
        if (username.length < 3 || password.length < 6) {
            return res.status(400).json({ error: 'Username must be at least 3 characters, password at least 6 characters' });
        }
        // Use a transaction to prevent race conditions
        db.prepare('BEGIN').run();
        try {
            // Check if users already exist (only allow one user)
            const hasUsers = userDb.hasUsers();
            if (hasUsers) {
                db.prepare('ROLLBACK').run();
                return res.status(403).json({ error: 'User already exists. This is a single-user system.' });
            }
            // Hash password
            const saltRounds = 12;
            const passwordHash = await bcrypt.hash(password, saltRounds);
            // Create user
            const user = userDb.createUser(username, passwordHash);"""
AUTH_REG_NEW = r"""        const { username, password, displayName } = req.body;
        // Validate input
        if (!username || !password) {
            return res.status(400).json({ error: 'Username and password are required' });
        }
        if (username.length < 3 || password.length < 6) {
            return res.status(400).json({ error: 'Username must be at least 3 characters, password at least 6 characters' });
        }
        // Username becomes the user's jail directory name, so it must be one safe path segment.
        if (!/^[a-zA-Z0-9_-]{3,32}$/.test(username)) {
            return res.status(400).json({ error: 'Username may contain only letters, digits, underscore and hyphen (3-32 chars)' });
        }
        const jail = computeUserJail(username);
        if (!jail) {
            return res.status(400).json({ error: 'Invalid username' });
        }
        // Use a transaction to prevent race conditions
        db.prepare('BEGIN').run();
        try {
            // Multi-user: duplicate usernames are rejected by the UNIQUE constraint
            // (SQLITE_CONSTRAINT_UNIQUE branch below), so there is no single-user gate.
            // Hash password
            const saltRounds = 12;
            const passwordHash = await bcrypt.hash(password, saltRounds);
            // Create user
            const user = userDb.createUser(username, passwordHash);
""" + JAIL_MKDIR_NEW + r"""            // New users skip onboarding: the shared Claude account is already logged in (HOME=/root).
            db.prepare('UPDATE users SET has_completed_onboarding = 1 WHERE id = ?').run(user.id);
            // Optional display name -> git identity (email left empty).
            if (typeof displayName === 'string' && displayName.trim()) {
                userDb.updateGitConfig(user.id, displayName.trim(), '');
            }"""

# A panel installed before the jail got its 0700 mode carries JAIL_MKDIR_OLD on disk: neither
# the pristine anchor nor the current text. Lift that piece first, then the replacement below
# sees the file as fully patched and skips it.
upgrade('routes/auth.js', JAIL_MKDIR_OLD, JAIL_MKDIR_NEW)

# The registration block keeps growing, so it is matched by a line of ours that does not
# change (the jail lookup) instead of by the whole block.
AUTH_REG_MARK = "        const jail = computeUserJail(username);"

patch('routes/auth.js', [
    (AUTH_FS_OLD, AUTH_FS_NEW, 1, AUTH_FS_MARK),
    (AUTH_JAIL_IMPORT_OLD, AUTH_JAIL_IMPORT_NEW, 1, AUTH_JAIL_IMPORT_MARK),
    (AUTH_REG_OLD, AUTH_REG_NEW, 1, AUTH_REG_MARK),
])

# =====================================================================
# 4) modules/projects/projects.routes.js
# =====================================================================
PR_IMPORT_OLD = "import { applyLegacyStarredProjectIds, toggleProjectStar } from '../../modules/projects/services/project-star.service.js';"
PR_IMPORT_NEW = r"""import { applyLegacyStarredProjectIds, toggleProjectStar } from '../../modules/projects/services/project-star.service.js';
import { projectsDb, userDb } from '../../modules/database/index.js';
import { computeUserJail, isPathInsideJail } from '../../shared/utils.js';"""

PR_PARAM_OLD = r"""const router = express.Router();
function readQueryStringValue(value) {"""
PR_PARAM_NEW = r"""const router = express.Router();
// Every :projectId route on this router is confined to the caller's jail.
// authenticateToken is applied at mount (index.js), so req.user is set before this runs.
router.param('projectId', async (req, res, next, value) => {
    try {
        const jail = computeUserJail(req.user?.username);
        if (!jail) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        const projectPath = projectsDb.getProjectPathById(value);
        if (!projectPath || !(await isPathInsideJail(projectPath, jail))) {
            return res.status(404).json({ error: 'Project not found' });
        }
        next();
    }
    catch {
        return res.status(403).json({ error: 'Forbidden' });
    }
});
// Auto-provision the caller's own jail folder as a project so users never have to
// create anything or type a path. Idempotent, best-effort: skips if a row already
// exists, and never throws out of the list handler.
async function ensureDefaultProjectForUser(user) {
    try {
        const jail = computeUserJail(user?.username);
        if (!jail) {
            return;
        }
        const existing = projectsDb.getProjectPath(jail);
        if (existing) {
            return;
        }
        const gitConfig = user?.id != null ? userDb.getGitConfig(user.id) : null;
        await createProject({ projectPath: jail, customName: gitConfig?.git_name || null });
    }
    catch {
        // Best-effort: a failed provision must not break the project list.
    }
}
function readQueryStringValue(value) {"""

PR_LIST_OLD = r"""    const projects = await getProjectsWithSessions({
        skipSynchronization,
        sessionsLimit,
        sessionsOffset,
    });
    res.json(projects);"""
PR_LIST_NEW = r"""    await ensureDefaultProjectForUser(req.user);
    const projects = await getProjectsWithSessions({
        skipSynchronization,
        sessionsLimit,
        sessionsOffset,
    });
    const jail = computeUserJail(req.user?.username);
    const filtered = [];
    for (const project of projects) {
        if (jail && await isPathInsideJail(project.fullPath, jail)) {
            filtered.push(project);
        }
    }
    res.json(filtered);"""

PR_ARCH_OLD = r"""router.get('/archived', asyncHandler(async (_req, res) => {
    const projects = await getArchivedProjectsWithSessions();
    res.json(createApiSuccessResponse({ projects }));
}));"""
PR_ARCH_NEW = r"""router.get('/archived', asyncHandler(async (req, res) => {
    const projects = await getArchivedProjectsWithSessions();
    const jail = computeUserJail(req.user?.username);
    const filtered = [];
    for (const project of projects) {
        if (jail && await isPathInsideJail(project.fullPath, jail)) {
            filtered.push(project);
        }
    }
    res.json(createApiSuccessResponse({ projects: filtered }));
}));"""

PR_CREATE_OLD = r"""    const projectCreationResult = await createProject({
        projectPath,
        customName,
    });"""
PR_CREATE_NEW = r"""    const jail = computeUserJail(req.user?.username);
    if (!jail || !projectPath || !(await isPathInsideJail(projectPath, jail))) {
        throw new AppError('Project path is outside your workspace', {
            code: 'PROJECT_PATH_FORBIDDEN',
            statusCode: 403,
        });
    }
    const projectCreationResult = await createProject({
        projectPath,
        customName,
    });"""

PR_CLONE_OLD = r"""        const authenticatedUser = req.user;
        const userId = authenticatedUser?.id;
        if (userId === undefined || userId === null) {
            throw new AppError('Authenticated user is required', {
                code: 'AUTHENTICATION_REQUIRED',
                statusCode: 401,
            });
        }"""
PR_CLONE_NEW = r"""        const authenticatedUser = req.user;
        const userId = authenticatedUser?.id;
        if (userId === undefined || userId === null) {
            throw new AppError('Authenticated user is required', {
                code: 'AUTHENTICATION_REQUIRED',
                statusCode: 401,
            });
        }
        const jail = computeUserJail(authenticatedUser?.username);
        if (!jail || !workspacePath || !(await isPathInsideJail(workspacePath, jail))) {
            throw new AppError('Workspace path is outside your workspace', {
                code: 'WORKSPACE_PATH_FORBIDDEN',
                statusCode: 403,
            });
        }"""

patch('modules/projects/projects.routes.js', [
    (PR_IMPORT_OLD, PR_IMPORT_NEW, 1),
    (PR_PARAM_OLD, PR_PARAM_NEW, 1),
    (PR_LIST_OLD, PR_LIST_NEW, 1),
    (PR_ARCH_OLD, PR_ARCH_NEW, 1),
    (PR_CREATE_OLD, PR_CREATE_NEW, 1),
    (PR_CLONE_OLD, PR_CLONE_NEW, 1),
])

# =====================================================================
# 5) routes/git.js  --  router-level jail for all /api/git/* endpoints
# =====================================================================
GIT_IMPORT_OLD = "import { projectsDb } from '../modules/database/index.js';"
GIT_IMPORT_NEW = r"""import { projectsDb } from '../modules/database/index.js';
import { computeUserJail, isPathInsideJail } from '../shared/utils.js';"""

GIT_GUARD_OLD = r"""const router = express.Router();
const COMMIT_DIFF_CHARACTER_LIMIT = 500_000;"""
GIT_GUARD_NEW = r"""const router = express.Router();
// Every git endpoint resolves its cwd from the `project` (projectId) param via
// getActualProjectPath. Confine that projectId to the caller's jail once, here.
router.use(async (req, res, next) => {
    try {
        const rawProject = req.query?.project ?? req.body?.project;
        if (rawProject === undefined || rawProject === null || rawProject === '') {
            return next();
        }
        const jail = computeUserJail(req.user?.username);
        const projectPath = jail ? projectsDb.getProjectPathById(String(rawProject)) : null;
        if (!jail || !projectPath || !(await isPathInsideJail(projectPath, jail))) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        next();
    }
    catch {
        return res.status(403).json({ error: 'Forbidden' });
    }
});
const COMMIT_DIFF_CHARACTER_LIMIT = 500_000;"""

patch('routes/git.js', [
    (GIT_IMPORT_OLD, GIT_IMPORT_NEW, 1),
    (GIT_GUARD_OLD, GIT_GUARD_NEW, 1),
])

# =====================================================================
# 6) routes/taskmaster.js  --  router.param jail + prd fileName traversal guard
# =====================================================================
TM_IMPORT_OLD = "import { projectsDb } from '../modules/database/index.js';"
TM_IMPORT_NEW = r"""import { projectsDb } from '../modules/database/index.js';
import { computeUserJail, isPathInsideJail } from '../shared/utils.js';"""

TM_PARAM_OLD = r"""const router = express.Router();
/**
 * Check if TaskMaster CLI is installed globally"""
TM_PARAM_NEW = r"""const router = express.Router();
router.param('projectId', async (req, res, next, value) => {
    try {
        const jail = computeUserJail(req.user?.username);
        const projectPath = jail ? projectsDb.getProjectPathById(value) : null;
        if (!jail || !projectPath || !(await isPathInsideJail(projectPath, jail))) {
            return res.status(404).json({ error: 'Project not found' });
        }
        next();
    }
    catch {
        return res.status(403).json({ error: 'Forbidden' });
    }
});
/**
 * Check if TaskMaster CLI is installed globally"""

TM_PRD_OLD = "        const filePath = path.join(projectPath, '.taskmaster', 'docs', fileName);"
TM_PRD_NEW = r"""        if (typeof fileName !== 'string' || fileName !== path.basename(fileName) || fileName.includes('..')) {
            return res.status(400).json({ error: 'Invalid file name' });
        }
        const docsRoot = path.resolve(projectPath, '.taskmaster', 'docs');
        const filePath = path.resolve(docsRoot, fileName);
        if (filePath !== docsRoot && !filePath.startsWith(docsRoot + path.sep)) {
            return res.status(400).json({ error: 'Invalid file name' });
        }"""

patch('routes/taskmaster.js', [
    (TM_IMPORT_OLD, TM_IMPORT_NEW, 1),
    (TM_PARAM_OLD, TM_PARAM_NEW, 1),
    (TM_PRD_OLD, TM_PRD_NEW, 1),
])

# =====================================================================
# 7) routes/commands.js  --  router-level jail for projectPath inputs
# =====================================================================
CMD_IMPORT_OLD = 'import { parseFrontMatter } from "../shared/frontmatter.js";'
CMD_IMPORT_NEW = r"""import { parseFrontMatter } from "../shared/frontmatter.js";
import { computeUserJail, isPathInsideJail } from "../shared/utils.js";"""

CMD_GUARD_OLD = r"""const router = express.Router();
const MODEL_PROVIDERS = ["claude", "cursor", "codex", "gemini", "opencode"];"""
CMD_GUARD_NEW = r"""const router = express.Router();
router.use(async (req, res, next) => {
    try {
        const candidates = [req.body?.projectPath, req.body?.context?.projectPath]
            .filter((value) => typeof value === "string" && value.length > 0);
        if (candidates.length === 0) {
            return next();
        }
        const jail = computeUserJail(req.user?.username);
        if (!jail) {
            return res.status(403).json({ error: "Forbidden" });
        }
        for (const candidate of candidates) {
            if (!(await isPathInsideJail(candidate, jail))) {
                return res.status(403).json({ error: "Forbidden" });
            }
        }
        next();
    }
    catch {
        return res.status(403).json({ error: "Forbidden" });
    }
});
const MODEL_PROVIDERS = ["claude", "cursor", "codex", "gemini", "opencode"];"""

patch('routes/commands.js', [
    (CMD_IMPORT_OLD, CMD_IMPORT_NEW, 1),
    (CMD_GUARD_OLD, CMD_GUARD_NEW, 1),
])

# =====================================================================
# 8) routes/gemini.js  --  jail the DELETE session-by-id
# =====================================================================
GEM_IMPORT_OLD = "import { sessionsDb } from '../modules/database/index.js';"
GEM_IMPORT_NEW = r"""import { sessionsDb } from '../modules/database/index.js';
import { computeUserJail, isPathInsideJail } from '../shared/utils.js';"""

GEM_DEL_OLD = r"""        if (!sessionId || typeof sessionId !== 'string' || !/^[a-zA-Z0-9_.-]{1,100}$/.test(sessionId)) {
            return res.status(400).json({ success: false, error: 'Invalid session ID format' });
        }
        await sessionManager.deleteSession(sessionId);"""
GEM_DEL_NEW = r"""        if (!sessionId || typeof sessionId !== 'string' || !/^[a-zA-Z0-9_.-]{1,100}$/.test(sessionId)) {
            return res.status(400).json({ success: false, error: 'Invalid session ID format' });
        }
        const jail = computeUserJail(req.user?.username);
        const sessionRow = jail ? sessionsDb.getSessionById(sessionId) : null;
        if (!jail || !sessionRow || !(await isPathInsideJail(sessionRow.project_path, jail))) {
            return res.status(404).json({ success: false, error: 'Session not found' });
        }
        await sessionManager.deleteSession(sessionId);"""

patch('routes/gemini.js', [
    (GEM_IMPORT_OLD, GEM_IMPORT_NEW, 1),
    (GEM_DEL_OLD, GEM_DEL_NEW, 1),
])

# =====================================================================
# 9) modules/providers/provider.routes.js
# =====================================================================
PROV_IMPORT_OLD = r"""import { sessionsService } from '../../modules/providers/services/sessions.service.js';
import { AppError, asyncHandler, createApiSuccessResponse } from '../../shared/utils.js';"""
PROV_IMPORT_NEW = r"""import { sessionsService } from '../../modules/providers/services/sessions.service.js';
import { sessionsDb } from '../../modules/database/index.js';
import { AppError, asyncHandler, computeUserJail, createApiSuccessResponse, isPathInsideJail } from '../../shared/utils.js';"""

PROV_PARAM_OLD = r"""const router = express.Router();
const readPathParam = (value, name) => {"""
PROV_PARAM_NEW = r"""const router = express.Router();
router.param('sessionId', async (req, res, next, value) => {
    try {
        const jail = computeUserJail(req.user?.username);
        const sessionRow = jail ? sessionsDb.getSessionById(String(value)) : null;
        if (!jail || !sessionRow || !(await isPathInsideJail(sessionRow.project_path, jail))) {
            return res.status(404).json({ success: false, error: 'Session not found' });
        }
        next();
    }
    catch {
        return res.status(403).json({ error: 'Forbidden' });
    }
});
router.use(async (req, res, next) => {
    try {
        const candidates = [req.query?.workspacePath, req.body?.workspacePath, req.body?.projectPath]
            .filter((value) => typeof value === 'string' && value.length > 0);
        if (candidates.length === 0) {
            return next();
        }
        const jail = computeUserJail(req.user?.username);
        if (!jail) {
            return res.status(403).json({ error: 'Forbidden' });
        }
        for (const candidate of candidates) {
            if (!(await isPathInsideJail(candidate, jail))) {
                return res.status(403).json({ error: 'Forbidden' });
            }
        }
        next();
    }
    catch {
        return res.status(403).json({ error: 'Forbidden' });
    }
});
const readPathParam = (value, name) => {"""

PROV_RUN_OLD = r"""router.get('/sessions/running', asyncHandler(async (_req, res) => {
    const sessions = sessionsService.listRunningSessions();
    res.json(createApiSuccessResponse({ sessions }));
}));"""
PROV_RUN_NEW = r"""router.get('/sessions/running', asyncHandler(async (req, res) => {
    const jail = computeUserJail(req.user?.username);
    const allRuns = sessionsService.listRunningSessions();
    const sessions = [];
    for (const run of allRuns) {
        const row = jail && run?.sessionId ? sessionsDb.getSessionById(String(run.sessionId)) : null;
        if (row && await isPathInsideJail(row.project_path, jail)) {
            sessions.push(run);
        }
    }
    res.json(createApiSuccessResponse({ sessions }));
}));"""

PROV_ARCH_OLD = r"""router.get('/sessions/archived', asyncHandler(async (_req, res) => {
    const sessions = sessionsService.listArchivedSessions();
    res.json(createApiSuccessResponse({ sessions }));
}));"""
PROV_ARCH_NEW = r"""router.get('/sessions/archived', asyncHandler(async (req, res) => {
    const jail = computeUserJail(req.user?.username);
    const allSessions = sessionsService.listArchivedSessions();
    const sessions = [];
    for (const session of allSessions) {
        if (jail && await isPathInsideJail(session.projectPath, jail)) {
            sessions.push(session);
        }
    }
    res.json(createApiSuccessResponse({ sessions }));
}));"""

PROV_SEARCH_OLD = r"""        await sessionConversationsSearchService.search({
            query,
            limit,
            signal: abortController.signal,
            onProgress: ({ projectResult, totalMatches, scannedProjects, totalProjects }) => {
                if (closed) {
                    return;
                }
                if (projectResult) {
                    res.write(`event: result\ndata: ${JSON.stringify({ projectResult, totalMatches, scannedProjects, totalProjects })}\n\n`);
                    return;
                }"""
PROV_SEARCH_NEW = r"""        const jail = computeUserJail(req.user?.username);
        await sessionConversationsSearchService.search({
            query,
            limit,
            signal: abortController.signal,
            onProgress: async ({ projectResult, totalMatches, scannedProjects, totalProjects }) => {
                if (closed) {
                    return;
                }
                if (projectResult) {
                    if (!jail || !(await isPathInsideJail(projectResult.projectName, jail))) {
                        return;
                    }
                    res.write(`event: result\ndata: ${JSON.stringify({ projectResult, totalMatches, scannedProjects, totalProjects })}\n\n`);
                    return;
                }"""

patch('modules/providers/provider.routes.js', [
    (PROV_IMPORT_OLD, PROV_IMPORT_NEW, 1),
    (PROV_PARAM_OLD, PROV_PARAM_NEW, 1),
    (PROV_RUN_OLD, PROV_RUN_NEW, 1),
    (PROV_ARCH_OLD, PROV_ARCH_NEW, 1),
    (PROV_SEARCH_OLD, PROV_SEARCH_NEW, 1),
])

# =====================================================================
# 10) modules/websocket/services/websocket-server.service.js  --  forward request to shell
# =====================================================================
WSS_OLD = r"""        if (pathname === '/shell') {
            handleShellConnection(ws, dependencies.shell);
            return;
        }"""
WSS_NEW = r"""        if (pathname === '/shell') {
            handleShellConnection(ws, incomingRequest, dependencies.shell);
            return;
        }"""

patch('modules/websocket/services/websocket-server.service.js', [
    (WSS_OLD, WSS_NEW, 1),
])

# =====================================================================
# 11) modules/websocket/services/shell-websocket.service.js  --  PTY cwd jail
# =====================================================================
SHELL_IMPORT_OLD = "import { parseIncomingJsonObject } from '../../../shared/utils.js';"
SHELL_IMPORT_NEW = "import { computeUserJail, isPathInsideJail, parseIncomingJsonObject } from '../../../shared/utils.js';"

SHELL_SIG_OLD = "export function handleShellConnection(ws, dependencies) {"
SHELL_SIG_NEW = "export function handleShellConnection(ws, request, dependencies) {"

SHELL_GUARD_OLD = "                const projectPath = readString(data.projectPath, process.cwd());"
SHELL_GUARD_NEW = r"""                const projectPath = readString(data.projectPath, process.cwd());
                const shellJail = computeUserJail(request?.user?.username);
                if (!shellJail || !(await isPathInsideJail(projectPath, shellJail))) {
                    ws.send(JSON.stringify({ type: 'error', message: 'Project path is outside your workspace' }));
                    return;
                }"""

patch('modules/websocket/services/shell-websocket.service.js', [
    (SHELL_IMPORT_OLD, SHELL_IMPORT_NEW, 1),
    (SHELL_SIG_OLD, SHELL_SIG_NEW, 1),
    (SHELL_GUARD_OLD, SHELL_GUARD_NEW, 1),
])

# =====================================================================
# 12) modules/websocket/services/chat-websocket.service.js  --  force jailed cwd
# =====================================================================
CHAT_IMPORT_OLD = r"""import { sessionsDb } from '../../../modules/database/index.js';
import { chatRunRegistry } from '../../../modules/websocket/services/chat-run-registry.service.js';
import { connectedClients, WS_OPEN_STATE } from '../../../modules/websocket/services/websocket-state.service.js';
import { parseIncomingJsonObject } from '../../../shared/utils.js';"""
CHAT_IMPORT_NEW = r"""import { sessionsDb, userDb } from '../../../modules/database/index.js';
import { chatRunRegistry } from '../../../modules/websocket/services/chat-run-registry.service.js';
import { connectedClients, WS_OPEN_STATE } from '../../../modules/websocket/services/websocket-state.service.js';
import { computeUserJail, isPathInsideJail, parseIncomingJsonObject } from '../../../shared/utils.js';"""

CHAT_CWD_OLD = r"""    const runtimeOptions = {
        ...clientOptions,
        sessionId: session.provider_session_id ?? undefined,
        resume: Boolean(session.provider_session_id),
        cwd: clientOptions.cwd ?? session.project_path ?? undefined,
        projectPath: session.project_path ?? clientOptions.projectPath,
    };"""
CHAT_CWD_NEW = r"""    const jailUser = userDb.getUserById(userId);
    const jail = computeUserJail(jailUser?.username);
    if (!jail || !session.project_path || !(await isPathInsideJail(session.project_path, jail))) {
        chatRunRegistry.completeRun(sessionId, { exitCode: 1 });
        sendProtocolError(ws, 'PROJECT_PATH_FORBIDDEN', 'Session project path is outside your workspace.', sessionId);
        return;
    }
    const runtimeOptions = {
        ...clientOptions,
        sessionId: session.provider_session_id ?? undefined,
        resume: Boolean(session.provider_session_id),
        cwd: session.project_path,
        projectPath: session.project_path,
    };"""

# Remember each chat socket's username so broadcasters can jail per-recipient.
CHAT_CONN_OLD = r"""    console.log('[INFO] Chat WebSocket connected');
    connectedClients.add(ws);
    const userId = readRequestUserId(request);"""
CHAT_CONN_NEW = r"""    console.log('[INFO] Chat WebSocket connected');
    const userId = readRequestUserId(request);
    try {
        ws.__jailUsername = userDb.getUserById(userId)?.username ?? null;
    }
    catch {
        ws.__jailUsername = null;
    }
    connectedClients.add(ws);"""

patch('modules/websocket/services/chat-websocket.service.js', [
    (CHAT_IMPORT_OLD, CHAT_IMPORT_NEW, 1),
    (CHAT_CWD_OLD, CHAT_CWD_NEW, 1),
    (CHAT_CONN_OLD, CHAT_CONN_NEW, 1),
])

# =====================================================================
# 13) modules/providers/services/sessions-watcher.service.js
#     Jail the realtime session_upserted broadcast. The watcher sees ALL users'
#     transcripts under /root/.claude; without this it pushed every session delta
#     (project + chat title) into every open sidebar. Now each delta only reaches
#     clients whose jail contains the session's project path.
# =====================================================================
patch('modules/providers/services/sessions-watcher.service.js', [
    (
        "import { generateDisplayName } from '../../../modules/projects/index.js';\n",
        "import { generateDisplayName } from '../../../modules/projects/index.js';\n"
        "import { computeUserJail, isPathInsideJail } from '../../../shared/utils.js';\n",
        1,
    ),
    (
        "    return JSON.stringify({\n        kind: 'session_upserted',\n",
        "    return { projectPath, payload: JSON.stringify({\n        kind: 'session_upserted',\n",
        1,
    ),
    (
        "        timestamp: new Date().toISOString(),\n    });\n}\nasync function flushPendingWatcherUpdate() {\n",
        "        timestamp: new Date().toISOString(),\n    }) };\n}\nasync function flushPendingWatcherUpdate() {\n",
        1,
    ),
    (
        "        if (events.length > 0) {\n"
        "            connectedClients.forEach(client => {\n"
        "                if (client.readyState === WS_OPEN_STATE) {\n"
        "                    for (const event of events) {\n"
        "                        client.send(event);\n"
        "                    }\n"
        "                }\n"
        "            });\n"
        "        }\n",
        "        if (events.length > 0) {\n"
        "            for (const client of connectedClients) {\n"
        "                if (client.readyState !== WS_OPEN_STATE) {\n"
        "                    continue;\n"
        "                }\n"
        "                const jail = computeUserJail(client.__jailUsername);\n"
        "                if (!jail) {\n"
        "                    continue;\n"
        "                }\n"
        "                for (const event of events) {\n"
        "                    if (event.projectPath && await isPathInsideJail(event.projectPath, jail)) {\n"
        "                        client.send(event.payload);\n"
        "                    }\n"
        "                }\n"
        "            }\n"
        "        }\n",
        1,
    ),
])

# =====================================================================
# 14) modules/websocket/services/chat-run-registry.service.js
#     Jail broadcastCanonicalSessionUpsert the same way (fires on run upsert).
# =====================================================================
patch('modules/websocket/services/chat-run-registry.service.js', [
    (
        "import { connectedClients, WS_OPEN_STATE } from '../../../modules/websocket/services/websocket-state.service.js';\n",
        "import { connectedClients, WS_OPEN_STATE } from '../../../modules/websocket/services/websocket-state.service.js';\n"
        "import { computeUserJail, isPathInsideJail } from '../../../shared/utils.js';\n",
        1,
    ),
    (
        "    connectedClients.forEach((client) => {\n"
        "        if (client.readyState === WS_OPEN_STATE) {\n"
        "            client.send(payload);\n"
        "        }\n"
        "    });\n"
        "}\n",
        "    for (const client of connectedClients) {\n"
        "        if (client.readyState !== WS_OPEN_STATE) {\n"
        "            continue;\n"
        "        }\n"
        "        const jail = computeUserJail(client.__jailUsername);\n"
        "        if (jail && projectPath && await isPathInsideJail(projectPath, jail)) {\n"
        "            client.send(payload);\n"
        "        }\n"
        "    }\n"
        "}\n",
        1,
    ),
])

# =====================================================================
# 15) modules/projects/services/projects-with-sessions-fetch.service.js
#     The broadcast loading_progress event carried currentProject: projectPath,
#     leaking other users' folder path (= username) to every sidebar. Drop it.
# =====================================================================
patch('modules/projects/services/projects-with-sessions-fetch.service.js', [
    (
        "            current: processedProjects,\n"
        "            total: totalProjects,\n"
        "            currentProject: projectPath,\n"
        "        });\n",
        "            current: processedProjects,\n"
        "            total: totalProjects,\n"
        "        });\n",
        1,
    ),
])

# =====================================================================
print("\n".join(report))
print("")
print(f"MODIFIED {len(modified)} files:")
for m in modified:
    print(f"  {m}")
print("PATCH DONE")
