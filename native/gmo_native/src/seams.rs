//! Pass 1 of `model_screen.simplify` (consistent seam simplification): port of `_seams`.
//!
//! The Python walk depends on the iteration order of Python `set`s (which chain is walked
//! first, in which direction), so [`PySet`] reproduces CPython 3.11's set table exactly: same
//! slots, probing and resize rules, hence the same iteration order. Float code follows numpy on
//! the reference machine: length-3 dots are BLAS ddot (`0.0 + a0*b0 + a1*b1 + a2*b2`, no fused
//! multiply-add); `(k,3) @ (3,)` for k > 1 is OpenBLAS dgemv (`fma(a2,b2, fma(a0,b0, a1*b1))`);
//! `norm(x, axis=1)` is `sqrt((x0*x0 + x1*x1) + x2*x2)`; `np.clip` passes NaN through.
//!
//! Nothing here may panic (the DLL is built with panic=abort): every index is validated up front
//! and inconsistencies return an error so the caller falls back to Python.

use std::collections::HashSet;

const EMPTY: i64 = -1;
const LINEAR_PROBES: usize = 9;
const PERTURB_SHIFT: u32 = 5;

/// CPython 3.11 `set` of non-negative ints (hash = value), add-only.
#[derive(Clone)]
pub struct PySet {
    table: Vec<i64>,
    mask: usize,
    fill: usize,
    used: usize,
}

impl Default for PySet {
    fn default() -> Self {
        Self::new()
    }
}

impl PySet {
    pub fn new() -> Self {
        PySet { table: vec![EMPTY; 8], mask: 7, fill: 0, used: 0 }
    }

    pub fn len(&self) -> usize {
        self.used
    }

    pub fn is_empty(&self) -> bool {
        self.used == 0
    }

    #[inline]
    fn next_slot(i: usize, perturb: &mut usize, mask: usize) -> usize {
        *perturb >>= PERTURB_SHIFT;
        (i.wrapping_mul(5).wrapping_add(1).wrapping_add(*perturb)) & mask
    }

    /// set_add_entry
    pub fn add(&mut self, key: i64) {
        let mask = self.mask;
        let mut perturb = key as usize;
        let mut i = (key as usize) & mask;
        loop {
            let probes = if i + LINEAR_PROBES <= mask { LINEAR_PROBES } else { 0 };
            for j in i..=i + probes {
                let e = self.table[j];
                if e == EMPTY {
                    self.table[j] = key;
                    self.fill += 1;
                    self.used += 1;
                    if self.fill * 5 < mask * 3 {
                        return;
                    }
                    let minused = if self.used > 50000 { self.used * 2 } else { self.used * 4 };
                    self.resize(minused);
                    return;
                }
                if e == key {
                    return;
                }
            }
            i = Self::next_slot(i, &mut perturb, mask);
        }
    }

    /// set_lookkey (no dummies: a key is always met before the first empty slot of its probe path)
    pub fn contains(&self, key: i64) -> bool {
        let mask = self.mask;
        let mut perturb = key as usize;
        let mut i = (key as usize) & mask;
        loop {
            let probes = if i + LINEAR_PROBES <= mask { LINEAR_PROBES } else { 0 };
            for j in i..=i + probes {
                let e = self.table[j];
                if e == EMPTY {
                    return false;
                }
                if e == key {
                    return true;
                }
            }
            i = Self::next_slot(i, &mut perturb, mask);
        }
    }

    /// set_table_resize + set_insert_clean
    fn resize(&mut self, minused: usize) {
        let mut newsize = 8usize;
        while newsize <= minused {
            newsize <<= 1;
        }
        let old = std::mem::replace(&mut self.table, vec![EMPTY; newsize]);
        self.mask = newsize - 1;
        let mask = self.mask;
        for key in old.into_iter().filter(|&k| k != EMPTY) {
            let mut perturb = key as usize;
            let mut i = (key as usize) & mask;
            'probe: loop {
                if self.table[i] == EMPTY {
                    self.table[i] = key;
                    break 'probe;
                }
                if i + LINEAR_PROBES <= mask {
                    for j in i + 1..=i + LINEAR_PROBES {
                        if self.table[j] == EMPTY {
                            self.table[j] = key;
                            break 'probe;
                        }
                    }
                }
                i = Self::next_slot(i, &mut perturb, mask);
            }
        }
        self.fill = self.used;
    }

    /// Python iteration order.
    pub fn iter(&self) -> impl Iterator<Item = i64> + '_ {
        self.table.iter().copied().filter(|&k| k != EMPTY)
    }
}

// ----------------------------------------------------------------------------- numpy arithmetic

#[inline]
fn dot3(a: [f64; 3], b: [f64; 3]) -> f64 {
    0.0 + a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

#[inline]
fn clip01(x: f64) -> f64 {
    if x.is_nan() {
        return x;
    }
    let x = if x > 0.0 { x } else { 0.0 };
    if x < 1.0 { x } else { 1.0 }
}

/// Python max(a, b) / min(a, b) for floats.
#[inline]
fn py_max(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

#[inline]
fn py_min(a: f64, b: f64) -> f64 {
    if b < a { b } else { a }
}

/// np.maximum / np.minimum (NaN propagates)
#[inline]
fn np_max(a: f64, b: f64) -> f64 {
    if a >= b || a.is_nan() { a } else { b }
}

#[inline]
fn np_min(a: f64, b: f64) -> f64 {
    if a <= b || a.is_nan() { a } else { b }
}

#[inline]
fn sub3(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

/// np.cross(o1 - o0, o2 - o0)
#[inline]
fn cross_sub(o0: [f64; 3], o1: [f64; 3], o2: [f64; 3]) -> [f64; 3] {
    let a = sub3(o1, o0);
    let b = sub3(o2, o0);
    [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

/// `model_screen.simplify.douglas_peucker` on 3-D points.
pub fn douglas_peucker(points: &[[f64; 3]], eps: f64) -> Vec<bool> {
    let n = points.len();
    let mut keep = vec![false; n];
    if n == 0 {
        return keep;
    }
    keep[0] = true;
    keep[n - 1] = true;
    let mut stack = vec![(0usize, n - 1)];
    let mut dist: Vec<f64> = Vec::new();
    while let Some((a, b)) = stack.pop() {
        if b <= a + 1 {
            continue;
        }
        let p = points[a];
        let d = sub3(points[b], p);
        let l2 = dot3(d, d);
        let m = b - a - 1;
        dist.clear();
        for j in 0..m {
            let s = sub3(points[a + 1 + j], p);
            let r = if l2 < 1e-24 {
                s
            } else {
                let dv = if m == 1 {
                    dot3(s, d) // a single row goes through ddot
                } else {
                    s[2].mul_add(d[2], s[0].mul_add(d[0], s[1] * d[1])) // dgemv
                };
                let t = clip01(dv / l2);
                [s[0] - t * d[0], s[1] - t * d[1], s[2] - t * d[2]]
            };
            dist.push(((r[0] * r[0] + r[1] * r[1]) + r[2] * r[2]).sqrt());
        }
        // np.argmax: first NaN, else first maximum
        let mut k = 0usize;
        for (j, &x) in dist.iter().enumerate() {
            if x.is_nan() {
                k = j;
                break;
            }
            if x > dist[k] {
                k = j;
            }
        }
        if dist[k] > eps {
            let mid = a + 1 + k;
            keep[mid] = true;
            stack.push((a, mid));
            stack.push((mid, b));
        }
    }
    keep
}

// ----------------------------------------------------------------------------- components

/// `model_screen.simplify.components`: label of every node = the smallest node index of its
/// connected component (what numpy's min-label propagation converges to). Returns None for an
/// edge with an index outside 0..n.
pub fn components(n: usize, edges: &[[i64; 2]]) -> Option<Vec<i64>> {
    let mut parent: Vec<usize> = (0..n).collect();
    fn find(parent: &mut [usize], mut x: usize) -> usize {
        let mut root = x;
        while parent[root] != root {
            root = parent[root];
        }
        while parent[x] != root {
            let next = parent[x];
            parent[x] = root;
            x = next;
        }
        root
    }
    for e in edges {
        if e[0] < 0 || e[1] < 0 || e[0] as usize >= n || e[1] as usize >= n {
            return None;
        }
        let (ra, rb) = (find(&mut parent, e[0] as usize), find(&mut parent, e[1] as usize));
        if ra < rb {
            parent[rb] = ra; // the root is always the smallest index of its set
        } else if rb < ra {
            parent[ra] = rb;
        }
    }
    Some((0..n).map(|i| find(&mut parent, i) as i64).collect())
}

// ----------------------------------------------------------------------------- seams

pub struct Input<'a> {
    pub pos: &'a [f64],      // nV*3 vertex positions (float64 of the float32 VVD values)
    pub nrm: &'a [f64],      // nV*3
    pub uv: &'a [f64],       // nV*2
    pub wid: &'a [i64],      // nV welded id per vertex
    pub bkey: &'a [i64],     // nV bone key per vertex
    pub uv_lim: &'a [f64],   // nV
    pub pw: &'a [f64],       // nW*3 welded positions
    pub comp: &'a [i64],     // nW component label per welded id
    pub comp_eps: &'a [f64], // nW epsilon per component label (only labels in use are read)
    pub cos_n: f64,
    pub floor: f64,
    pub rel_size: f64,
}

#[derive(Debug, PartialEq, Eq)]
pub enum SeamsError {
    BadIndex,
    Inconsistent,
}

struct State<'a> {
    inp: &'a Input<'a>,
    tris: &'a mut [[i64; 3]],
    alive: &'a mut [bool],
    v2t: Vec<PySet>,
    w2v: Vec<Vec<i64>>,
}

impl<'a> State<'a> {
    #[inline]
    fn pos(&self, v: i64) -> [f64; 3] {
        let i = v as usize * 3;
        [self.inp.pos[i], self.inp.pos[i + 1], self.inp.pos[i + 2]]
    }

    #[inline]
    fn nrm(&self, v: i64) -> [f64; 3] {
        let i = v as usize * 3;
        [self.inp.nrm[i], self.inp.nrm[i + 1], self.inp.nrm[i + 2]]
    }

    #[inline]
    fn uv(&self, v: i64) -> [f64; 2] {
        let i = v as usize * 2;
        [self.inp.uv[i], self.inp.uv[i + 1]]
    }

    #[inline]
    fn pw(&self, w: i64) -> [f64; 3] {
        let i = w as usize * 3;
        [self.inp.pw[i], self.inp.pw[i + 1], self.inp.pw[i + 2]]
    }

    #[inline]
    fn wid(&self, v: i64) -> i64 {
        self.inp.wid[v as usize]
    }

    /// Neighbours of v that share exactly one live triangle with it, in Python's dict order.
    fn border_neighbors(&self, v: i64, out: &mut Vec<i64>) {
        let mut cnt: Vec<(i64, u32)> = Vec::with_capacity(16);
        for ti in self.v2t[v as usize].iter() {
            let ti = ti as usize;
            if !self.alive[ti] {
                continue;
            }
            for &x in self.tris[ti].iter() {
                if x != v {
                    match cnt.iter_mut().find(|e| e.0 == x) {
                        Some(e) => e.1 += 1,
                        None => cnt.push((x, 1)),
                    }
                }
            }
        }
        out.clear();
        out.extend(cnt.iter().filter(|e| e.1 == 1).map(|e| e.0));
    }

    fn walk_to(&self, u: i64, chain: &[i64], i: usize, k_next: usize) -> Option<i64> {
        let mut cur = u;
        let mut bn = Vec::new();
        for &target in &chain[i + 1..=k_next] {
            self.border_neighbors(cur, &mut bn);
            let mut found = None;
            let mut count = 0;
            for &x in &bn {
                if self.wid(x) == target {
                    count += 1;
                    found = Some(x);
                }
            }
            if count != 1 {
                return None;
            }
            cur = found?;
        }
        Some(cur)
    }

    fn remove_point(&mut self, chain: &[i64], i: usize, k_prev: usize, k_next: usize) -> Result<bool, SeamsError> {
        let (q, a, b) = (chain[i], chain[k_prev], chain[k_next]);
        let (pa, pb, pq) = (self.pw(a), self.pw(b), self.pw(q));
        let d = sub3(pb, pa);
        let l2 = dot3(d, d);
        let t = if l2 > 1e-12 { clip01(dot3(sub3(pq, pa), d) / l2) } else { 0.0 };
        let s = 1.0 - t;
        let cos_n = self.inp.cos_n;
        let mut plans: Vec<(i64, i64)> = Vec::new();
        let mut bn = Vec::new();
        let members = self.w2v[q as usize].clone();
        for &u in &members {
            self.border_neighbors(u, &mut bn);
            let mut tgt = Vec::new();
            for &x in &bn {
                if self.wid(x) == chain[i - 1] {
                    tgt.push(x);
                }
            }
            if bn.len() != 2 || tgt.len() != 1 {
                return Ok(false);
            }
            let v = tgt[0];
            let nxt = match self.walk_to(u, chain, i, k_next) {
                Some(x) => x,
                None => return Ok(false),
            };
            let (nr, nn) = (self.nrm(v), self.nrm(nxt));
            let mut n_i = [s * nr[0] + t * nn[0], s * nr[1] + t * nn[1], s * nr[2] + t * nn[2]];
            let m = py_max(dot3(n_i, n_i).sqrt(), 1e-12);
            n_i = [n_i[0] / m, n_i[1] / m, n_i[2] / m];
            if dot3(n_i, self.nrm(u)) < cos_n {
                return Ok(false);
            }
            let (ur, un, uu) = (self.uv(v), self.uv(nxt), self.uv(u));
            let e0 = (s * ur[0] + t * un[0] - uu[0]).abs();
            let e1 = (s * ur[1] + t * un[1] - uu[1]).abs();
            if !e0.is_nan() && !e1.is_nan() && (if e0 >= e1 { e0 } else { e1 }) > self.inp.uv_lim[u as usize] {
                return Ok(false);
            }
            let pv = self.pos(v);
            for ti in self.v2t[u as usize].iter() {
                let ti = ti as usize;
                if !self.alive[ti] {
                    continue;
                }
                let tri = self.tris[ti];
                if tri.contains(&v) {
                    continue;
                }
                let old = [self.pos(tri[0]), self.pos(tri[1]), self.pos(tri[2])];
                let mut new = old;
                let k = tri.iter().position(|&x| x == u).ok_or(SeamsError::Inconsistent)?;
                new[k] = pv;
                let n0 = cross_sub(old[0], old[1], old[2]);
                let n1 = cross_sub(new[0], new[1], new[2]);
                let (l0, l1) = (dot3(n0, n0).sqrt(), dot3(n1, n1).sqrt());
                if l1 < 1e-10 || (l0 > 1e-10 && dot3(n0, n1) / (l0 * l1) < 0.5) {
                    return Ok(false);
                }
            }
            plans.push((u, v));
        }
        for (u, v) in plans {
            let list: Vec<i64> = self.v2t[u as usize].iter().collect();
            for ti in list {
                let tu = ti as usize;
                if !self.alive[tu] {
                    continue;
                }
                if self.tris[tu].contains(&v) {
                    self.alive[tu] = false;
                    continue;
                }
                let k = self.tris[tu].iter().position(|&x| x == u).ok_or(SeamsError::Inconsistent)?;
                self.tris[tu][k] = v;
                self.v2t[v as usize].add(ti);
            }
            self.v2t[u as usize] = PySet::new();
        }
        self.w2v[q as usize].clear();
        Ok(true)
    }
}

/// Runs pass 1 in place: rewrites `tris` and `alive` exactly like `_seams` and returns the
/// number of removed seam points.
pub fn seams(inp: &Input, tris: &mut [[i64; 3]], alive: &mut [bool]) -> Result<i64, SeamsError> {
    let n_v = inp.wid.len();
    let n_w = inp.comp.len();
    if inp.pos.len() != n_v * 3 || inp.nrm.len() != n_v * 3 || inp.uv.len() != n_v * 2 || inp.bkey.len() != n_v
        || inp.uv_lim.len() != n_v || inp.pw.len() != n_w * 3 || inp.comp_eps.len() != n_w || alive.len() != tris.len()
    {
        return Err(SeamsError::BadIndex);
    }
    if tris.iter().flatten().any(|&v| v < 0 || v as usize >= n_v)
        || inp.wid.iter().any(|&w| w < 0 || w as usize >= n_w)
        || inp.comp.iter().any(|&c| c < 0 || c as usize >= n_w)
        || tris.len() > i64::MAX as usize / 2
    {
        return Err(SeamsError::BadIndex);
    }

    // v2t: for i, t in enumerate(T): for v in t: v2t[v].add(i)
    let mut v2t: Vec<PySet> = (0..n_v).map(|_| PySet::new()).collect();
    for (i, t) in tris.iter().enumerate() {
        for &v in t {
            v2t[v as usize].add(i as i64);
        }
    }
    // w2v: for v in np.unique(T): w2v[wid[v]].append(v)  (dict keeps first-insertion order)
    let mut used = vec![false; n_v];
    for &v in tris.iter().flatten() {
        used[v as usize] = true;
    }
    let mut w2v: Vec<Vec<i64>> = vec![Vec::new(); n_w];
    let mut w_has = vec![false; n_w];
    let mut w_keys: Vec<i64> = Vec::new();
    for (v, _) in used.iter().enumerate().filter(|e| *e.1) {
        let w = inp.wid[v];
        if !w_has[w as usize] {
            w_has[w as usize] = true;
            w_keys.push(w);
        }
        w2v[w as usize].push(v as i64);
    }

    let mut st = State { inp, tris, alive, v2t, w2v };

    // adj (dict of sets, insertion order)
    let mut adj: Vec<Option<PySet>> = vec![None; n_w];
    let mut adj_keys: Vec<i64> = Vec::new();
    let mut bn = Vec::new();
    for &q in &w_keys {
        for k in 0..st.w2v[q as usize].len() {
            let v = st.w2v[q as usize][k];
            st.border_neighbors(v, &mut bn);
            for &x in &bn {
                let (a, b) = (st.wid(v), st.wid(x));
                if a != b {
                    for (from, to) in [(a, b), (b, a)] {
                        let slot = &mut adj[from as usize];
                        if slot.is_none() {
                            *slot = Some(PySet::new());
                            adj_keys.push(from);
                        }
                        if let Some(set) = slot.as_mut() {
                            set.add(to);
                        }
                    }
                }
            }
        }
    }
    let adj_len = |q: i64| adj[q as usize].as_ref().map_or(0, |s| s.len());
    let mut corner = PySet::new();
    for &q in &adj_keys {
        if adj_len(q) != 2 {
            corner.add(q);
        }
    }
    for &q in &w_keys {
        if adj[q as usize].is_some() {
            let vs = &st.w2v[q as usize];
            if let Some(&first) = vs.first() {
                let k0 = inp.bkey[first as usize];
                if vs.iter().any(|&v| inp.bkey[v as usize] != k0) {
                    corner.add(q);
                }
            }
        }
    }
    let mut starts: Vec<i64> = corner.iter().collect();
    starts.extend(adj_keys.iter().copied().filter(|&q| !corner.contains(q)));

    let mut seen: HashSet<(i64, i64)> = HashSet::new();
    let mut removed = 0i64;
    let mut chain: Vec<i64> = Vec::new();
    let mut pts: Vec<[f64; 3]> = Vec::new();
    for &c in &starts {
        let nbs: Vec<i64> = match adj[c as usize].as_ref() {
            Some(s) => s.iter().collect(),
            None => continue,
        };
        for nb in nbs {
            if seen.contains(&(c, nb)) {
                continue;
            }
            chain.clear();
            chain.push(c);
            chain.push(nb);
            seen.insert((c, nb));
            seen.insert((nb, c));
            loop {
                let last = chain[chain.len() - 1];
                if corner.contains(last) || last == c {
                    break;
                }
                let prev = chain[chain.len() - 2];
                let nxt = adj[last as usize].as_ref().and_then(|s| s.iter().find(|&x| x != prev));
                match nxt {
                    None => break,
                    Some(x) => {
                        seen.insert((last, x));
                        seen.insert((x, last));
                        chain.push(x);
                    }
                }
            }
            if chain.len() < 3 {
                continue;
            }
            pts.clear();
            pts.extend(chain.iter().map(|&w| st.pw(w)));
            let mut mx = pts[0];
            let mut mn = pts[0];
            for p in &pts[1..] {
                for k in 0..3 {
                    mx[k] = np_max(mx[k], p[k]);
                    mn[k] = np_min(mn[k], p[k]);
                }
            }
            let cext = sub3(mx, mn);
            let cmax = np_max(np_max(cext[0], cext[1]), cext[2]);
            let label = inp.comp[chain[0] as usize];
            let eps = py_min(inp.comp_eps[label as usize], py_max(inp.rel_size * 0.5 * cmax, inp.floor));
            let mut keep = douglas_peucker(&pts, eps);
            let n = chain.len();
            let mut k_prev = 0usize;
            for i in 1..n - 1 {
                if keep[i] {
                    k_prev = i;
                    continue;
                }
                let mut k_next = i + 1;
                while k_next < n - 1 && !keep[k_next] {
                    k_next += 1;
                }
                if st.remove_point(&chain, i, k_prev, k_next)? {
                    removed += 1;
                } else {
                    keep[i] = true;
                    k_prev = i;
                }
            }
        }
    }
    Ok(removed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pyset_small_ints_follow_cpython_slots() {
        // {2, 9}: 2 -> slot 2, 9 -> slot 1, so Python iterates 9 then 2
        let mut s = PySet::new();
        s.add(2);
        s.add(9);
        assert_eq!(s.iter().collect::<Vec<_>>(), vec![9, 2]);
        assert!(s.contains(9) && s.contains(2) && !s.contains(3));
        // growing past 5 entries resizes to 32 slots (fill*5 >= mask*3)
        for k in [40, 41, 3, 100] {
            s.add(k);
        }
        assert_eq!(s.len(), 6);
        assert_eq!(s.mask, 31);
        assert_eq!(s.iter().collect::<Vec<_>>(), vec![2, 3, 100, 40, 9, 41]); // Python 3.11: list(s)
    }

    #[test]
    fn components_label_with_the_smallest_index() {
        let lab = components(7, &[[5, 2], [2, 6], [1, 3], [4, 4]]).unwrap();
        assert_eq!(lab, vec![0, 1, 2, 1, 4, 2, 2]);
        assert!(components(3, &[[0, 3]]).is_none());
    }

    #[test]
    fn douglas_peucker_keeps_the_corner() {
        let pts = [[0.0, 0.0, 0.0], [1.0, 0.001, 0.0], [2.0, 0.0, 0.0], [2.0, 1.0, 0.0], [2.0, 2.0, 0.0]];
        let keep = douglas_peucker(&pts, 0.01);
        assert_eq!(keep, vec![true, false, true, false, true]);
    }
}
