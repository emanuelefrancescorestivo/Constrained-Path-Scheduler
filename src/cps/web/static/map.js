// The study map (DECISIONS.md D133): Leaflet over OpenStreetMap's tiles, the places and
// who is at each as the server says (the page's data), the list below working without
// this script. Layout and input only: a tap on the map fills the "add a place" form,
// "Near me" asks the device where it is and only moves the map (the position is never
// sent), "Study here" posts the place. The tile address comes from the page, as the tile
// policy asks; tiles send the site's origin as their referrer, never a plan's address.
// Under reduced motion the map neither glides nor fades (D132).

const box = document.getElementById("study-map");
const L = window.L;

function still() {
  return (
    matchMedia("(prefers-reduced-motion: reduce)").matches || document.documentElement.dataset.motion === "reduce"
  );
}

function popup(place, words, action, canStudy) {
  const root = document.createElement("div");
  root.className = "map-pop";
  const name = document.createElement("b");
  name.textContent = place.name;
  const line = document.createElement("span");
  line.textContent = place.line;
  root.append(name, line);
  if (place.me) {
    const here = document.createElement("span");
    here.className = "badge badge-accent";
    here.textContent = words.you;
    root.append(here);
  } else if (canStudy) {
    const form = document.createElement("form");
    form.method = "post";
    form.action = action;
    const field = document.createElement("input");
    field.type = "hidden";
    field.name = "place";
    field.value = place.id;
    const button = document.createElement("button");
    button.type = "submit";
    button.className = place.named.length ? "btn btn-sm btn-primary" : "btn btn-sm";
    button.textContent = place.named.length ? words.join : words.here;
    form.append(field, button);
    root.append(form);
  }
  return root;
}

function pin(place) {
  const count = Number(place.count) || 0;
  return L.divIcon({
    className: `map-pin${count ? "" : " is-empty"}${place.me ? " is-me" : ""}`,
    html: count ? `<span>${count}</span>` : "<span></span>",
    iconSize: count ? [34, 34] : [18, 18],
    iconAnchor: count ? [17, 17] : [9, 9],
    popupAnchor: [0, count ? -16 : -8],
  });
}

if (box && L) {
  const data = box.dataset;
  const words = JSON.parse(data.words || "{}");
  const places = JSON.parse(data.places || "[]");
  const calm = still();
  box.hidden = false;
  document.querySelector("[data-map-tools]")?.removeAttribute("hidden");
  document.querySelector(".map-noscript")?.setAttribute("hidden", "");
  const map = L.map(box, {
    zoomAnimation: !calm,
    fadeAnimation: !calm,
    markerZoomAnimation: !calm,
    inertia: !calm,
    scrollWheelZoom: false,
  }).setView([Number(data.lat), Number(data.lon)], Number(data.zoom) || 13);
  const link = '<a href="https://www.openstreetmap.org/copyright" rel="noopener noreferrer" target="_blank">OpenStreetMap</a>';
  L.tileLayer(data.tiles, {
    maxZoom: 19,
    referrerPolicy: "origin",
    attribution: (words.attribution || "© {link} contributors").replace("{link}", link),
  }).addTo(map);

  const markers = new Map();
  for (const place of places) {
    const marker = L.marker([place.lat, place.lon], { icon: pin(place), title: place.name, keyboard: true })
      .addTo(map)
      .bindPopup(popup(place, words, data.action, data.canStudy === "1"));
    markers.set(place.id, marker);
  }
  if (data.at && markers.has(data.at)) markers.get(data.at).openPopup();

  // A tap on the map: a new place there, named in the form below the map.
  const form = document.getElementById("map-add");
  let draft = null;
  if (form && data.canAdd === "1") {
    map.on("click", (event) => {
      const { lat, lng } = event.latlng;
      document.getElementById("place-lat").value = lat.toFixed(5);
      document.getElementById("place-lon").value = lng.toFixed(5);
      if (draft) draft.setLatLng(event.latlng);
      else draft = L.marker(event.latlng, { icon: L.divIcon({ className: "map-pin is-draft", html: "<span></span>", iconSize: [18, 18] }) }).addTo(map);
      form.hidden = false;
      form.querySelector("input[name=name]").focus({ preventScroll: true });
      form.scrollIntoView({ block: "nearest", behavior: calm ? "auto" : "smooth" });
    });
    form.querySelector("[data-map-cancel]")?.addEventListener("click", () => {
      form.hidden = true;
      if (draft) {
        draft.remove();
        draft = null;
      }
    });
  }

  // Near me: the device's position moves the map, and goes nowhere else.
  const near = document.querySelector("[data-map-near]");
  if (near && "geolocation" in navigator) {
    near.hidden = false;
    near.addEventListener("click", () => {
      navigator.geolocation.getCurrentPosition(
        (where) => map.setView([where.coords.latitude, where.coords.longitude], 16, { animate: !calm }),
        () => {
          near.disabled = true;
        },
        { enableHighAccuracy: false, timeout: 10000, maximumAge: 600000 },
      );
    });
  }
}
