/* Tiny DOM helper — same shape as apps/pwa/js/views.js so contributors
 * learn one pattern across all OpenHealth client apps. */
export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "onclick") n.addEventListener("click", v);
    else if (k === "onchange") n.addEventListener("change", v);
    else if (k.startsWith("data-")) n.setAttribute(k, v);
    else n[k] = v;
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    n.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return n;
}
