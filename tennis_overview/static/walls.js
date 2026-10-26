"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const labels = {
    official_practice_wall: "Official practice wall",
    community_report: "Community report",
    handball_wall: "Handball; tennis unverified",
  };
  let catalog,
    map,
    markers = new Map(),
    selected = null,
    visible = [];
  const node = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = text;
    if (className) el.className = className;
    return el;
  };
  const link = (label, url) => {
    const el = node("a", label);
    el.href = url;
    el.target = "_blank";
    el.rel = "noopener noreferrer";
    return el;
  };
  function sources(row, target) {
    const list = node("ul", undefined, "sources");
    row.sources.forEach((source) => {
      const li = node("li");
      li.append(link(source.label, source.url));
      list.append(li);
    });
    target.append(
      list,
      node("p", "Sources reviewed " + row.reviewed_on, "reviewed"),
    );
  }
  function select(id, pan = true) {
    const focused = document.activeElement?.getAttribute("aria-controls");
    selected = id;
    renderList();
    if (focused)
      document
        .querySelector('[aria-controls="' + focused + '"]')
        ?.focus({ preventScroll: true });
    markers.forEach((marker, key) => {
      const el = marker.getElement();
      if (el) {
        el.classList.toggle("active", key === id);
        el.setAttribute("aria-pressed", String(key === id));
      }
    });
    if (pan && map && markers.has(id))
      map.stop().setView(markers.get(id).getLatLng(), 15, { animate: false });
  }
  function renderList() {
    $("results").replaceChildren();
    if (!visible.length)
      $("results").append(
        node("p", "No mapped locations match these filters.", "muted"),
      );
    visible.forEach((row) => {
      const article = node(
        "article",
        undefined,
        "location " + row.status + (row.id === selected ? " selected" : ""),
      );
      const button = node("button", undefined, "select-location");
      button.type = "button";
      button.setAttribute("aria-expanded", String(row.id === selected));
      button.setAttribute("aria-controls", "detail-" + row.id);
      button.append(
        node("span", String(row.number), "number"),
        node("span", row.name),
      );
      button.addEventListener("click", () => {
        select(row.id);
        $("detail-" + row.id).scrollIntoView({
          block: "nearest",
          behavior: "smooth",
        });
      });
      const heading = node("h3");
      heading.append(button);
      article.append(
        heading,
        node("p", labels[row.status], "badge"),
        node("p", row.address, "address"),
      );
      const detail = node("div", undefined, "details");
      detail.id = "detail-" + row.id;
      detail.hidden = row.id !== selected;
      detail.append(
        node("p", row.notes),
        node(
          "p",
          row.precision === "venue"
            ? "Approximate venue pin, not the exact wall."
            : "Documented wall position.",
          "muted",
        ),
      );
      const directions = node("div", undefined, "directions");
      // Route to the venue address, not a park centroid that may have no entrance.
      const destination = encodeURIComponent(row.name + ", " + row.address);
      directions.append(
        link("Apple Maps", "https://maps.apple.com/?daddr=" + destination),
        link(
          "Google Maps",
          "https://www.google.com/maps/dir/?api=1&destination=" + destination,
        ),
      );
      detail.append(directions);
      sources(row, detail);
      detail.append(link("Coordinate source", row.coordinate_source));
      article.append(detail);
      $("results").append(article);
    });
  }
  function matches(row) {
    const query = $("search").value.trim().toLowerCase();
    return (
      (!$("city").value || row.city === $("city").value) &&
      (!query ||
        [row.name, row.city, row.address || ""]
          .join(" ")
          .toLowerCase()
          .includes(query))
    );
  }
  function fit() {
    if (map && visible.length)
      map.stop().fitBounds(
        visible.map((row) => [row.lat, row.lon]),
        { padding: [40, 40], maxZoom: 14, animate: false },
      );
  }
  function render() {
    visible = catalog.locations.filter(
      (row) =>
        matches(row) &&
        (!$("evidence").value || row.status === $("evidence").value),
    );
    if (!visible.some((row) => row.id === selected))
      selected = visible[0]?.id || null;
    markers.forEach((marker) => marker.remove());
    markers.clear();
    if (map) {
      fit();
      visible.forEach((row) => {
        const marker = L.marker([row.lat, row.lon], {
          title: row.name,
          alt: row.name,
          icon: L.divIcon({
            className: "wall-pin " + row.status,
            html: String(row.number),
            iconSize: [32, 32],
            iconAnchor: [16, 16],
          }),
        }).addTo(map);
        marker.getElement().setAttribute("aria-label", row.name);
        const tooltip = node("span", row.name);
        marker.bindTooltip(tooltip);
        marker.on("click", () => {
          select(row.id, false);
          $("detail-" + row.id).scrollIntoView({
            block: "nearest",
            behavior: "smooth",
          });
        });
        markers.set(row.id, marker);
      });
    }
    $("fit").disabled = !visible.length;
    select(selected, false);
    $("count").textContent =
      visible.length + " of " + catalog.locations.length + " mapped locations";
    $("leads").replaceChildren();
    const leads = catalog.leads.filter(matches);
    leads.forEach((row) => {
      const item = node("details", undefined, "lead");
      item.append(
        node("summary", row.name),
        node("p", row.city, "muted"),
        node("p", row.notes),
      );
      sources(row, item);
      $("leads").append(item);
    });
    if (!leads.length)
      $("leads").append(
        node("p", "No unmapped leads match this search.", "muted"),
      );
  }
  async function start() {
    try {
      const response = await fetch(document.body.dataset.api);
      if (!response.ok) throw new Error("catalog");
      catalog = await response.json();
      catalog.locations.forEach((row, index) => {
        row.number = index + 1;
      });
      [
        ...new Set(
          [...catalog.locations, ...catalog.leads].map((row) => row.city),
        ),
      ]
        .sort()
        .forEach((city) => {
          const option = node("option", city);
          option.value = city;
          $("city").append(option);
        });
      if (window.L && catalog.locations.length) {
        map = L.map("map", { scrollWheelZoom: false });
        $("fit").hidden = false;
        $("fit").addEventListener("click", fit);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        })
          .on("tileerror", () => {
            $("map-status").textContent =
              "Some map tiles could not load. Location details and navigation links remain available.";
            $("map-status").hidden = false;
          })
          .addTo(map);
      } else if (!window.L) {
        $("map-status").textContent =
          "Map could not load. Location details and navigation links are available below.";
        $("map-status").hidden = false;
      } else {
        $("map").textContent = "No mapped locations in this catalog.";
      }
      render();
      $("search").addEventListener("input", render);
      $("city").addEventListener("change", render);
      $("evidence").addEventListener("change", render);
    } catch (_) {
      $("count").textContent = "Locations unavailable";
      $("error").textContent =
        "The location catalog could not be loaded. Reload to retry or ask the operator to check the catalog.";
      $("error").hidden = false;
    }
  }
  start();
})();
