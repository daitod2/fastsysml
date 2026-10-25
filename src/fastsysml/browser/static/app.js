// The page of the SysML browser: search and lists (center), metaclasses and the ownership tree (left), the
// details of an element with its relationships (right). The state is in the URL hash (q: query, e: element key,
// v: view), so that back and forward work and each element has a link. The API is in server.py.
"use strict";

// The words shown on the page (field names, groups of metaclasses). Metaclasses and edge kinds are shown as
// they are (see term()).
const TERMS = {
  columns: {
    label: "名前", original: "元の名前", metaclass: "メタクラス", owner: "所有者", typing: "型", subclassification: "特化元",
    redefinition: "再定義", subsetting: "部分集合", reference: "参照", usages: "使用数", multiplicity: "多重度",
    value: "値", doc: "ドキュメント", file: "ファイル", line: "行",
  },
  groups: {
    Package: "パッケージ", Definition: "定義", Usage: "使用", Relationship: "関係", AnnotatingElement: "注釈",
    Element: "その他",
  },
};

// The language shown ("en": the originals; else that of the translations of the server) and its glossary.
let lang = "en";
let glossary = {};

// A metaclass or edge kind for display.
function term(name) {
  if (name == null) return name;
  return (glossary.metaclasses || {})[name] || (glossary.edges || {})[name] || name;
}

const $ = (selector) => document.querySelector(selector);

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (name.startsWith("on")) el.addEventListener(name.slice(2), value);
    else if (name === "class") el.className = value;
    else el.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

async function api(path, params, method, body) {
  const url = new URL(path, location.href);
  for (const [name, value] of Object.entries(params || {})) url.searchParams.set(name, value);
  if (!url.searchParams.has("lang")) url.searchParams.set("lang", lang);
  const options = { method: method || "GET" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

// State and navigation

// What is shown (undefined: nothing yet, so that it is loaded whatever the URL says).
let current = { q: undefined, e: undefined, v: undefined };
let typing = false; // Whether the search box was edited since the last navigation (then edits replace it).

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  return { q: params.get("q") || "", e: params.get("e"), v: params.get("v") };
}

function hashOf(state) {
  const params = new URLSearchParams();
  if (state.q) params.set("q", state.q);
  if (state.e) params.set("e", state.e);
  if (state.v) params.set("v", state.v);
  return "#" + params.toString();
}

function navigate(changes, replace) {
  const state = Object.assign({}, current, changes);
  if (replace) history.replaceState(null, "", hashOf(state));
  else history.pushState(null, "", hashOf(state));
  route();
}

// A link to an element (keeping the query and view).
function link(node, text) {
  if (!node) return h("span", { class: "error-text" }, text || "?");
  const title = [node.original, node.qualifiedName].filter(Boolean).join("\n");
  const a = h("a", { href: hashOf(Object.assign({}, current, { e: node.key })), title }, text || node.label);
  a.addEventListener("click", (event) => {
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    navigate({ e: node.key });
  });
  return a;
}

function chip(text, onclick, title) {
  if (!onclick) return h("span", { class: "chip", title }, text);
  return h("a", { class: "chip", href: "#", title, onclick: (event) => { event.preventDefault(); onclick(); } }, text);
}

function query(q) {
  typing = false;
  navigate({ q, v: null });
}

async function route() {
  const state = readHash();
  const previous = current;
  current = state;
  if (!typing) $("#search").value = state.q;
  if (state.q !== previous.q || state.v !== previous.v) loadList(state);
  if (state.e !== previous.e) loadDetail(state.e);
  else markSelected();
  renderTypes();
}

// Header

async function loadInfo() {
  const info = await api("api/info");
  const button = $("#lang-button");
  button.hidden = !info.language;
  button.textContent = lang === "en" ? "日本語で表示" : "原文（英語）で表示";
  button.dataset.language = info.language || "";
  $("#status").textContent = `ファイル ${info.files.length}・要素 ${info.nodes}・関係 ${info.edges}`;
  $("#status").title = info.files.join("\n");
  const files = $("#files-button");
  files.hidden = !info.root;
  files.textContent = !info.loaded.length ? "ファイルを選ぶ"
    : info.loaded.length === 1 ? `ファイル：${info.loaded[0].split("/").pop()}`
    : `ファイル：${commonFolder(info.loaded)}（${info.loaded.length}）`;
  files.title = ["表示するファイルを選ぶ"].concat(info.loaded).join("\n");
  const issues = $("#issues-button");
  issues.textContent = info.issues ? `エラー ${info.errors}・指摘 ${info.issues - info.errors}` : "指摘なし";
  issues.classList.toggle("has-errors", info.errors > 0);
  return info;
}

// Files (a dialog to choose the files shown, among those of the root directory of the server)

let fileList = null;

function commonFolder(paths) {
  const folders = paths.map((path) => path.split("/").slice(0, -1));
  const common = [];
  for (let i = 0; folders.every((folder) => i < folder.length && folder[i] === folders[0][i]); i++) {
    common.push(folders[0][i]);
  }
  return common.length ? common[common.length - 1] : "…";
}

async function showFiles() {
  const dialog = $("#files-dialog");
  if (!dialog.open) dialog.showModal();
  $("#files-status").textContent = "読み込み中…";
  fileList = await api("api/files");
  $("#files-status").textContent = `${fileList.root}（${fileList.files.length} ファイル）`;
  renderFiles();
  $("#files-filter").focus();
}

function renderFiles() {
  const filter = $("#files-filter").value.trim().toLowerCase();
  const loaded = new Set(fileList.loaded);
  const folders = new Map();
  for (const path of fileList.files) {
    if (filter && !path.toLowerCase().includes(filter)) continue;
    const folder = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ".";
    if (!folders.has(folder)) folders.set(folder, []);
    folders.get(folder).push(path);
  }
  const rows = [];
  for (const [folder, paths] of folders) {
    const all = fileList.files.filter((path) => (path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ".") === folder);
    rows.push(h("div", { class: "folder-row" }, h("span", null, folder, " ", h("span", { class: "count" }, all.length)),
      h("button", { type: "button", onclick: () => openFiles(all) }, "フォルダごと開く")));
    for (const path of paths) {
      rows.push(h("div", { class: "file-row" + (loaded.has(path) ? " loaded" : ""), role: "button", tabindex: "0",
                           title: path, onclick: () => openFiles([path]),
                           onkeydown: (event) => { if (event.key === "Enter") openFiles([path]); } },
        path.split("/").pop()));
    }
  }
  $("#files-list").replaceChildren(...(rows.length ? rows : [h("div", { class: "empty" }, "該当するファイルはありません。")]));
}

async function openFiles(paths) {
  const list = $("#files-list");
  list.classList.add("busy");
  $("#files-status").textContent = `${paths.length} ファイルを読み込み中…`;
  try {
    await api("api/open", null, "POST", { paths });
    $("#files-dialog").close();
    // The elements of other files are gone: keep the query, not the element.
    history.pushState(null, "", hashOf({ q: current.q, v: current.v }));
    await setLanguage(lang);
  } catch (error) {
    $("#files-status").textContent = `読み込めませんでした：${error.message || error}`;
  } finally {
    list.classList.remove("busy");
  }
}

function renderHelp() {
  const rows = [
    ["wheel", "名前・修飾名・ドキュメント・メタデータ・値に含む（大文字と小文字は区別しない、* はワイルドカード）"],
    ["type:PartUsage", "そのメタクラスかサブクラス（type:Definition ですべての定義）"],
    ["metaclass:PartUsage", "そのメタクラスだけ"],
    ["name:engine", "名前が一致（name:*wheel* のように * も使える）"],
    ["in:Vehicle::Parts", "修飾名で指定した名前空間の中（末尾が一致すればよい）"],
    ["meta:Safety", "そのメタデータが付いている"],
    ["file:Vehicle", "ファイル名に含む"],
    ["typing:P::Engine", "その要素への辺（ここでは typing）を持つ。辺の種類は詳細の関係の見出しを参照"],
  ];
  $("#help").replaceChildren(
    h("table", null, rows.map(([example, text]) => h("tr", null, h("td", null, h("code", null, example)),
                                                     h("td", null, text)))),
    h("p", null, "語や欄はスペースで区切り、すべてを満たすものを探します。値に空白を含むときは \"...\" で囲みます。"));
}

// Metaclasses (left)

let types = [];

async function loadTypes() {
  types = await api("api/types");
  renderTypes();
}

function renderTypes() {
  const groups = new Map(Object.keys(TERMS.groups).map((group) => [group, []]));
  for (const type of types) groups.get(type.group).push(type);
  const children = [];
  for (const [group, items] of groups) {
    if (!items.length) continue;
    const total = items.reduce((sum, item) => sum + item.count, 0);
    const groupQuery = `type:${group}`;
    children.push(h("div", { class: "group" },
      h("div", { class: "group-title" + (current.q === groupQuery ? " active" : ""), onclick: () => query(groupQuery),
                 title: groupQuery },
        h("span", null, TERMS.groups[group]), h("span", { class: "count" }, total)),
      items.map((item) => {
        const itemQuery = `metaclass:${item.metaclass}`;
        return h("div", { class: "facet" + (current.q === itemQuery ? " active" : ""), onclick: () => query(itemQuery),
                          title: `${item.metaclass}\n${itemQuery}` },
          h("span", null, term(item.metaclass)), h("span", { class: "count" }, item.count));
      })));
  }
  $("#types").replaceChildren(...children);
}

// Ownership tree (left)

const tree = { children: new Map(), expanded: new Set(), loaded: false };

async function treeChildren(key) {
  if (!tree.children.has(key)) tree.children.set(key, await api("api/children", key ? { key } : {}));
  return tree.children.get(key);
}

async function renderTree() {
  if ($("#tree").hidden) return;
  const roots = await treeChildren("");
  const rows = [];
  const add = (nodes, depth) => {
    for (const node of nodes) {
      const expanded = tree.expanded.has(node.key);
      const toggle = h("span", { class: "tree-toggle" }, node.children ? (expanded ? "▾" : "▸") : "");
      toggle.addEventListener("click", async (event) => {
        event.stopPropagation();
        if (expanded) tree.expanded.delete(node.key);
        else { tree.expanded.add(node.key); await treeChildren(node.key); }
        renderTree();
      });
      rows.push(h("div", { class: "tree-row" + (node.key === current.e ? " selected" : ""), "data-key": node.key,
                           style: `padding-left:${8 + depth * 14}px`,
                           title: [node.label, node.original, term(node.metaclass)].filter(Boolean).join("\n"),
                           onclick: () => navigate({ e: node.key }) },
        toggle, h("span", { class: "tree-label" }, node.label)));
      if (expanded && tree.children.has(node.key)) add(tree.children.get(node.key), depth + 1);
    }
  };
  add(roots, 0);
  $("#tree").replaceChildren(...rows);
  const selected = $("#tree .tree-row.selected");
  if (selected) selected.scrollIntoView({ block: "nearest" });
}

async function revealInTree(detail) {
  if (!detail) return;
  for (const ancestor of detail.ancestors) {
    tree.expanded.add(ancestor.key);
    await treeChildren(ancestor.key);
  }
  renderTree();
}

// List (center)

const list = { rows: [], total: 0, sort: null, descending: false, filter: "", shown: 300, view: null };

async function loadList(state) {
  list.view = state.v;
  list.sort = null;
  list.filter = "";
  list.shown = 300;
  $("#list").replaceChildren(h("div", { class: "loading" }, "読み込み中…"));
  try {
    if (state.v === "issues") {
      list.rows = (await api("api/issues")).map((issue) => Object.assign({ key: issue.node && issue.node.key }, issue));
      list.total = list.rows.length;
    } else {
      const result = await api("api/search", { q: state.q });
      list.rows = result.rows;
      list.total = result.total;
    }
    renderList();
  } catch (error) {
    $("#list").replaceChildren(h("div", { class: "empty error-text" }, String(error.message || error)));
  }
}

function columnsOf(rows) {
  if (list.view === "issues") return ["severity", "message", "node", "file", "line"];
  const columns = Object.keys(TERMS.columns).filter((column) =>
    ["label", "metaclass"].includes(column) || rows.some((row) => row[column] != null && row[column] !== ""));
  const files = new Set(rows.map((row) => row.file));
  return columns.filter((column) => column !== "file" || files.size > 1);
}

function columnTitle(column) {
  return { severity: "重大度", message: "内容", node: "要素" }[column] || TERMS.columns[column] || column;
}

function cell(row, column) {
  const value = row[column];
  if (column === "node") return h("td", null, value ? link(value) : "");
  if (column === "severity") return h("td", { class: value === "error" ? "error-text" : "warning-text" }, value);
  if (column === "metaclass") return h("td", null, term(value));
  const numeric = typeof value === "number";
  return h("td", { class: numeric ? "num" : null, title: typeof value === "string" ? value : null },
           value == null ? "" : value);
}

function renderList() {
  let rows = list.rows;
  if (list.filter) {
    const filter = list.filter.toLowerCase();
    rows = rows.filter((row) => Object.values(row).some((value) =>
      typeof value === "string" && value.toLowerCase().includes(filter)));
  }
  if (list.sort) {
    const column = list.sort;
    const sign = list.descending ? -1 : 1;
    rows = rows.slice().sort((a, b) => {
      const x = a[column], y = b[column];
      if (x == null) return y == null ? 0 : 1;
      if (y == null) return -1;
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * sign;
    });
  }
  const columns = columnsOf(list.rows);
  const title = list.view === "issues" ? "指摘" : current.q ? `「${current.q}」` : "すべての要素";
  const count = list.total > list.rows.length ? `${list.rows.length} / ${list.total} 件` : `${list.total} 件`;
  const filter = h("input", { type: "search", placeholder: "結果を絞り込む", value: list.filter,
                              "aria-label": "結果を絞り込む" });
  filter.addEventListener("input", () => {
    list.filter = filter.value;
    list.shown = 300;
    renderList();
    const input = $("#list .list-head input");
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
  const head = h("div", { class: "list-head" },
    h("span", { class: "list-title" }, title, " ", h("span", { class: "count" }, count)),
    filter,
    list.view === "issues" ? null :
      h("a", { href: "api/search.csv?q=" + encodeURIComponent(current.q), download: "sysml.csv" }, "CSV"));
  if (!rows.length) {
    $("#list").replaceChildren(head, h("div", { class: "empty" },
      list.view === "issues" ? "指摘はありません。" : "該当する要素はありません。"));
    return;
  }
  const header = h("tr", null, columns.map((column) => {
    const mark = list.sort === column ? (list.descending ? " ▼" : " ▲") : "";
    return h("th", { onclick: () => {
      if (list.sort === column) list.descending = !list.descending;
      else { list.sort = column; list.descending = false; }
      renderList();
    } }, columnTitle(column) + mark);
  }));
  const body = h("tbody", null, rows.slice(0, list.shown).map((row) =>
    h("tr", { "data-key": row.key || "", class: row.key && row.key === current.e ? "selected" : null,
              onclick: () => row.key && navigate({ e: row.key }) },
      columns.map((column) => cell(row, column)))));
  const table = h("table", { class: "rows", tabindex: "0" }, h("thead", null, header), body);
  table.addEventListener("keydown", (event) => moveSelection(event, rows));
  const more = rows.length > list.shown ?
    h("button", { class: "more", type: "button", onclick: () => { list.shown += 500; renderList(); } },
      `さらに表示（残り ${rows.length - list.shown} 件）`) : null;
  $("#list").replaceChildren(...[head, table, more].filter(Boolean));
}

function moveSelection(event, rows) {
  if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
  event.preventDefault();
  const visible = rows.slice(0, list.shown).filter((row) => row.key);
  const index = visible.findIndex((row) => row.key === current.e);
  const next = visible[Math.max(0, Math.min(visible.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
  if (next) navigate({ e: next.key }, true);
}

function markSelected() {
  for (const row of document.querySelectorAll("#list tbody tr")) {
    const selected = row.dataset.key && row.dataset.key === current.e;
    row.classList.toggle("selected", selected);
    if (selected) row.scrollIntoView({ block: "nearest" });
  }
  for (const row of document.querySelectorAll("#tree .tree-row")) {
    row.classList.toggle("selected", row.dataset.key === current.e);
  }
}

// Details (right)

let detailRequest = 0;

async function loadDetail(key) {
  markSelected();
  if (!key) {
    renderPlaceholder();
    return;
  }
  const request = ++detailRequest;
  $("#detail").replaceChildren(h("div", { class: "loading" }, "読み込み中…"));
  try {
    const detail = await api("api/node", { key });
    if (request !== detailRequest) return;
    renderDetail(detail);
    revealInTree(detail);
    if (detail.fetched) {
      loadInfo();
      loadTypes();
    }
  } catch (error) {
    if (request === detailRequest) {
      $("#detail").replaceChildren(h("div", { class: "empty error-text" }, String(error.message || error)));
    }
  }
}

function renderPlaceholder() {
  $("#detail").replaceChildren(h("div", { class: "placeholder" },
    h("h2", null, "要素を選んでください"),
    h("p", null, "一覧やツリーの要素を選ぶと、ここに詳細と関係が表示されます。関係の相手をクリックすると、その要素に移ります。"),
    h("p", null, "左の「種類」でメタクラスごとの一覧を、上の検索で名前や条件による一覧を出せます。")));
}

function section(title, count, ...content) {
  return h("section", null, h("h3", null, title, count != null ? h("span", { class: "count" }, count) : null), content);
}

function groupBy(items, key) {
  const groups = new Map();
  for (const item of items) {
    if (!groups.has(item[key])) groups.set(item[key], []);
    groups.get(item[key]).push(item);
  }
  return groups;
}

function edgeList(edges, incoming, detail) {
  const groups = groupBy(edges, "kind");
  return Array.from(groups, ([kind, items]) => {
    const toList = incoming && detail.qualifiedName && items.length > 1 ?
      h("button", { type: "button", title: `${kind}:${detail.qualifiedName}`,
                    onclick: () => query(`${kind}:"${detail.qualifiedName}"`) }, "一覧に出す") : null;
    return h("div", null,
      h("div", { class: "kind" }, h("span", { class: "kind-name" }, (incoming ? "← " : "→ ") + term(kind)),
        h("span", { class: "count" }, items.length), toList),
      h("ul", { class: "links" }, items.map((edge) => h("li", null,
        edge.unresolved ? h("span", { class: "error-text", title: "解決できなかった参照" }, edge.label)
          : link(edge.node, edge.label),
        edge.node && edge.node.metaclass ? chip(term(edge.node.metaclass)) : null,
        edge.node && edge.node.stub ? chip("未読み込み", null, "ライブラリなど、まだ読み込んでいない要素（開くと読み込みます）") : null,
        edge.via ? h("span", { class: "via" }, "経由 ", link(edge.via)) : null))));
  });
}

function sourceView(detail) {
  const lines = detail.text.split("\n");
  const max = 400;
  const pre = h("pre", { class: "source" });
  const render = (count) => {
    pre.replaceChildren(...lines.slice(0, count).map((line, i) =>
      h("div", null, h("span", { class: "ln" }, (detail.line || 1) + i), line)));
  };
  render(max);
  const more = lines.length > max ? h("button", { type: "button", class: "more", onclick: (event) => {
    render(lines.length);
    event.target.remove();
  } }, `すべて表示（${lines.length} 行）`) : null;
  return [pre, more];
}

function renderDetail(detail) {
  const props = [];
  const prop = (name, value) => {
    if (value != null && value !== "" && !(Array.isArray(value) && !value.length)) props.push([name, value]);
  };
  prop("修飾名", detail.qualifiedName);
  prop("元の名前", detail.original);
  prop("短い名前", detail.shortName);
  prop("メンバーシップ", detail.membership && term(detail.membership));
  prop("多重度", detail.multiplicity && h("code", null, detail.multiplicity));
  prop("値", detail.value && h("code", null, detail.value));
  prop("メタデータ", detail.metadata.map((name) => chip(name, () => query(`meta:${name}`), `meta:${name}`)));
  for (const [name, value] of Object.entries(detail.attributes || {})) prop(name, String(value));
  prop("ファイル", detail.line ? `${detail.file}：${detail.line} 行目` : detail.file);

  const crumbs = detail.ancestors.length ? h("div", { class: "crumbs" },
    detail.ancestors.map((ancestor, i) => [i ? h("span", { class: "sep" }, "›") : null, link(ancestor)])) : null;
  const actions = h("div", { class: "actions" },
    detail.metaclass ? h("button", { type: "button", onclick: () => query(`metaclass:${detail.metaclass}`) },
      /^[\x20-\x7e]*$/.test(term(detail.metaclass)) ? `${term(detail.metaclass)} の一覧` : `${term(detail.metaclass)}の一覧`)
      : null,
    detail.qualifiedName && detail.childCount ?
      h("button", { type: "button", onclick: () => query(`in:"${detail.qualifiedName}"`) }, "中の要素を一覧") : null);

  const children = detail.children.length ? section("所有する要素", detail.childCount,
    h("ul", { class: "links" }, detail.children.map((child) =>
      h("li", null, link(child), chip(term(child.metaclass)))))) : null;

  $("#detail").replaceChildren(h("div", { class: "detail" },
    crumbs,
    h("div", { class: "detail-title" }, h("h2", null, detail.label),
      detail.original ? h("span", { class: "original", title: "元の名前" }, detail.original) : null,
      detail.metaclass ? chip(term(detail.metaclass), () => query(`metaclass:${detail.metaclass}`)) : null,
      detail.stub ? chip("未読み込み") : null),
    actions,
    detail.issues.length ? section("指摘", detail.issues.length, h("ul", { class: "links" }, detail.issues.map(
      (issue) => h("li", { class: issue.severity === "error" ? "error-text" : "warning-text" },
                   `${issue.line}:${issue.column} ${issue.message}`)))) : null,
    detail.doc.length ? section("ドキュメント", null, detail.doc.map((doc) => h("p", { class: "doc" }, doc)),
      detail.docOriginal ? h("details", { class: "original-doc" }, h("summary", null, "原文"),
        detail.docOriginal.map((doc) => h("p", { class: "doc" }, doc))) : null) : null,
    section("基本", null, h("table", { class: "props" }, props.map(([name, value]) =>
      h("tr", null, h("th", null, name), h("td", null, value))))),
    detail.out.length ? section("出ていく関係", detail.out.length, edgeList(detail.out, false, detail)) : null,
    detail.in.length ? section("入ってくる関係", detail.in.length, edgeList(detail.in, true, detail)) : null,
    children,
    detail.text ? section("ソース", null, sourceView(detail)) : null,
    h("p", { class: "uri" }, detail.uri)));
  $("#detail").scrollTop = 0;
}

// Setup

function setup() {
  const search = $("#search");
  let timer = null;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      navigate({ q: search.value.trim(), v: null }, typing);
      typing = true;
    }, 300);
  });
  search.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      clearTimeout(timer);
      if (search.value.trim() !== current.q) navigate({ q: search.value.trim(), v: null }, typing);
      typing = false;
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== search && !(event.target instanceof HTMLInputElement)) {
      event.preventDefault();
      search.focus();
      search.select();
    }
    if (event.key === "Escape") $("#help").hidden = true;
  });
  $("#back").addEventListener("click", () => history.back());
  $("#forward").addEventListener("click", () => history.forward());
  $("#help-button").addEventListener("click", () => {
    const help = $("#help");
    help.hidden = !help.hidden;
    $("#help-button").setAttribute("aria-expanded", String(!help.hidden));
  });
  $("#issues-button").addEventListener("click", () => { typing = false; navigate({ v: "issues" }); });
  $("#reload").addEventListener("click", async () => {
    $("#reload").disabled = true;
    try {
      await api("api/reload", null, "POST");
      await setLanguage(lang);
    } finally {
      $("#reload").disabled = false;
    }
  });
  for (const tab of document.querySelectorAll(".tabs button")) {
    tab.addEventListener("click", () => {
      for (const other of document.querySelectorAll(".tabs button")) {
        other.setAttribute("aria-selected", String(other === tab));
        $("#" + other.dataset.tab).hidden = other !== tab;
      }
      if (tab.dataset.tab === "tree") renderTree();
    });
  }
  window.addEventListener("popstate", () => { typing = false; route(); });
  $("#lang-button").addEventListener("click", () => {
    setLanguage(lang === "en" ? $("#lang-button").dataset.language : "en");
  });
  $("#files-button").addEventListener("click", showFiles);
  $("#files-close").addEventListener("click", () => $("#files-dialog").close());
  $("#files-filter").addEventListener("input", renderFiles);
  renderHelp();
  start();
}

async function start() {
  const info = await api("api/info", { lang: "en" });
  let stored = null;
  try { stored = localStorage.getItem("sysml-browser-lang"); } catch (error) { /* (No storage.) */ }
  lang = info.language && stored !== "en" ? info.language : "en";
  await setLanguage(lang);
  if (info.root && !info.loaded.length) showFiles(); // Nothing to show yet.
}

let languageRequest = 0;

// Shows a language (reloading what is shown).
async function setLanguage(language) {
  const request = ++languageRequest;
  const terms = language === "en" ? {} : await api("api/glossary", { lang: language });
  if (request !== languageRequest) return; // (Switched again meanwhile.)
  lang = language;
  glossary = terms;
  try { localStorage.setItem("sysml-browser-lang", lang); } catch (error) { /* (No storage.) */ }
  tree.children.clear();
  current = { q: undefined, e: undefined, v: undefined };
  await Promise.all([loadInfo(), loadTypes()]);
  route();
  renderTree();
}

setup();
