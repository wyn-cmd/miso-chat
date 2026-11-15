// The account's generated scene pictures. Cards open in a viewer with the character, time, description
// and the conversation context that produced them. Every request goes through /api/gallery, which
// checks the signed-in account before serving an image.
const grid = $("galleryGrid");
let items = [];
let viewing = null;

function sceneTime(iso) {
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? "" : when.toLocaleString();
}

function card(item) {
  const btn = el("button", null, "gallery-card");
  btn.type = "button";
  const img = el("img", null, "gallery-card-img");
  img.src = item.url;
  img.alt = item.description || item.character || "Scene";
  img.loading = "lazy";
  const body = el("div", null, "gallery-card-body");
  body.appendChild(el("strong", item.character || "Scene", "gallery-card-title"));
  const when = sceneTime(item.created);
  if (when) body.appendChild(el("span", when, "gallery-card-date"));
  if (item.description) body.appendChild(el("p", item.description, "gallery-card-desc"));
  btn.appendChild(img);
  btn.appendChild(body);
  btn.addEventListener("click", () => openItem(item));
  return btn;
}

async function loadGallery() {
  $("galleryMsg").textContent = "";
  try {
    const data = await api("GET", "/api/gallery");
    items = data.items || [];
    grid.replaceChildren();
    if (!items.length) {
      $("galleryMsg").textContent = "No scene pictures yet. Generate one from a chat.";
      return;
    }
    for (const item of items) grid.appendChild(card(item));
  } catch (err) {
    $("galleryMsg").textContent = err.message;
  }
}

function openItem(item) {
  viewing = item;
  $("galleryViewTitle").textContent = item.character || "Scene";
  $("galleryViewImg").src = item.url;
  $("galleryViewImg").alt = item.description || "";
  $("galleryViewMeta").textContent = sceneTime(item.created);
  $("galleryViewDescription").textContent = item.description || "No description saved.";
  $("galleryViewContext").textContent = item.context || "No context saved.";
  if (!$("galleryView").open) $("galleryView").showModal();
}

$("galleryViewClose").addEventListener("click", () => $("galleryView").close());
$("galleryViewDelete").addEventListener("click", async () => {
  if (!viewing) return;
  if (!window.confirm("Delete this picture?")) return;
  try {
    await api("POST", "/api/gallery/" + encodeURIComponent(viewing.id) + "/delete");
    $("galleryView").close();
    await loadGallery();
  } catch (err) {
    $("galleryMsg").textContent = err.message;
  }
});
loadGallery().catch((err) => { $("galleryMsg").textContent = err.message; });
