"""Target hierarchy: load, save, roll up, and list rows in report order."""
from __future__ import annotations

from .util import key, now_iso

LEVEL_NAMES = {0: "National", 1: "Region", 2: "RSM", 3: "ASM", 4: "DTM", 5: "ASE", 6: "Territory"}


class Node:
    __slots__ = ("id", "level", "label", "gt_target", "smmt_target", "children", "parent", "distributor",
                 "csr", "m", "display")

    def __init__(self, id, level, label, gt=0.0, smmt=0.0, distributor=None, csr=None):
        self.id, self.level, self.label = id, level, label
        self.gt_target, self.smmt_target = gt or 0.0, smmt or 0.0
        self.distributor, self.csr = distributor, csr
        self.children: list[Node] = []
        self.parent: Node | None = None
        self.m: dict = {}          # metrics filled by the report engine
        self.display = label

    @property
    def is_territory(self):
        return self.level == 6

    @property
    def key(self):
        return key(self.label)

    @property
    def is_smmt(self):
        """SMMT territory = it carries an SMMT target and no GT target (or is named SMMT with no GT target)."""
        return self.is_territory and self.gt_target == 0 and (self.smmt_target > 0 or "smmt" in self.key)

    def territories(self):
        if self.is_territory:
            yield self
        for c in self.children:
            yield from c.territories()

    def ancestor(self, level):
        p = self.parent
        while p is not None:
            if p.level == level:
                return p
            p = p.parent
        return None

    def row_label(self):
        if self.level == 0:
            return "NATIONAL TOTAL"
        if self.is_territory:
            return self.display
        return f"{self.display}  ({LEVEL_NAMES[self.level]} Total)"

    def postorder(self):
        """Report order: children first, then this node's subtotal row (Famulus layout)."""
        for c in self.children:
            yield from c.postorder()
        yield self

    def rollup(self, additive: list[str]):
        """Sum additive metrics from children into every parent."""
        if self.is_territory:
            return
        for c in self.children:
            c.rollup(additive)
        for k in additive:
            self.m[k] = sum(c.m.get(k, 0.0) for c in self.children)


def load_tree(con, year: int, month: int) -> Node | None:
    up = con.execute("SELECT id FROM target_uploads WHERE year=? AND month=?", (year, month)).fetchone()
    if not up:
        return None
    rows = con.execute("SELECT * FROM target_nodes WHERE upload_id=? ORDER BY sort_order, id", (up["id"],)).fetchall()
    nodes = {r["id"]: Node(r["id"], r["level"], r["label"], r["gt_target"], r["smmt_target"], r["distributor"],
                           r["csr"]) for r in rows}
    root = None
    for r in rows:
        n = nodes[r["id"]]
        if r["parent_id"] is None:
            root = n
        else:
            p = nodes.get(r["parent_id"])
            if p:
                n.parent = p
                p.children.append(n)
    if root is None:
        return None
    overrides = {r["original_key"]: r["display_label"] for r in con.execute("SELECT * FROM label_override")}
    for n in nodes.values():
        n.display = overrides.get(n.key, n.label)
    root.rollup([])
    _sum_targets(root)
    return root


def _sum_targets(n: Node):
    if n.is_territory:
        return n.gt_target, n.smmt_target
    g = s = 0.0
    for c in n.children:
        a, b = _sum_targets(c)
        g += a
        s += b
    n.gt_target, n.smmt_target = g, s
    return g, s


def save_tree(con, year: int, month: int, root: dict, source_filename: str, source_kind: str) -> int:
    """root: nested dict {label, level, gt, smmt, children, distributor?, csr?}. Replaces the month."""
    ex = con.execute("SELECT id FROM target_uploads WHERE year=? AND month=?", (year, month)).fetchone()
    if ex:
        con.execute("DELETE FROM target_nodes WHERE upload_id=?", (ex["id"],))
        con.execute("DELETE FROM target_uploads WHERE id=?", (ex["id"],))
    cur = con.execute("INSERT INTO target_uploads(year, month, source_filename, source_kind, uploaded_at) "
                      "VALUES (?,?,?,?,?)", (year, month, source_filename, source_kind, now_iso()))
    up = cur.lastrowid

    def ins(nd, parent_id, order):
        terr = nd["level"] == 6
        c = con.execute(
            "INSERT INTO target_nodes(upload_id, parent_id, level, sort_order, label, gt_target, smmt_target,"
            " distributor, csr) VALUES (?,?,?,?,?,?,?,?,?)",
            (up, parent_id, nd["level"], order, nd["label"], nd.get("gt", 0) if terr else 0,
             nd.get("smmt", 0) if terr else 0, nd.get("distributor"), nd.get("csr")))
        for i, ch in enumerate(nd.get("children", [])):
            ins(ch, c.lastrowid, i)

    ins(root, None, 0)
    r = con.execute("SELECT SUM(gt_target) g, SUM(smmt_target) s FROM target_nodes WHERE upload_id=? AND level=6",
                    (up,)).fetchone()
    con.execute("UPDATE target_uploads SET gt_total=?, smmt_total=? WHERE id=?", (r["g"] or 0, r["s"] or 0, up))
    con.commit()
    return up


def tree_to_dict(n: Node) -> dict:
    return {"label": n.label, "level": n.level, "gt": n.gt_target if n.is_territory else 0,
            "smmt": n.smmt_target if n.is_territory else 0, "distributor": n.distributor, "csr": n.csr,
            "children": [tree_to_dict(c) for c in n.children]}


def copy_month(con, src: tuple[int, int], dst: tuple[int, int]) -> int:
    t = load_tree(con, *src)
    if t is None:
        raise ValueError("The source month has no targets.")
    return save_tree(con, dst[0], dst[1], tree_to_dict(t), f"copied from {src[0]}-{src[1]:02d}", "copy")


def set_territory_target(con, year, month, label, gt, smmt):
    up = con.execute("SELECT id FROM target_uploads WHERE year=? AND month=?", (year, month)).fetchone()
    con.execute("UPDATE target_nodes SET gt_target=?, smmt_target=? WHERE upload_id=? AND level=6 AND label=?",
                (gt, smmt, up["id"], label))
    r = con.execute("SELECT SUM(gt_target) g, SUM(smmt_target) s FROM target_nodes WHERE upload_id=? AND level=6",
                    (up["id"],)).fetchone()
    con.execute("UPDATE target_uploads SET gt_total=?, smmt_total=? WHERE id=?", (r["g"], r["s"], up["id"]))
    con.commit()
