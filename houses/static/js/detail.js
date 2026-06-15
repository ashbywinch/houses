(function () {
  "use strict";

  var STREET_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
  var SATELLITE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

  function initDetailMap() {
    var el = document.getElementById("detail-map");
    if (!el || el._leaflet_map) return;
    var lat = parseFloat(el.getAttribute("data-lat"));
    var lng = parseFloat(el.getAttribute("data-lng"));
    var mapUrl = el.getAttribute("data-url");
    if (isNaN(lat) || isNaN(lng)) return;

    var street = L.tileLayer(STREET_URL, { maxZoom: 19, attribution: "&copy; OpenStreetMap" });
    var satellite = L.tileLayer(SATELLITE_URL, { maxZoom: 19, attribution: "Esri" });

    var map = L.map(el, {
      center: [lat, lng],
      zoom: 15,
      layers: [street],
      zoomControl: true,
    });
    el._leaflet_map = map;

    L.marker([lat, lng]).addTo(map);

    // Satellite toggle
    var satCtrl = L.control({ position: "topright" });
    satCtrl.onAdd = function () {
      var btn = L.DomUtil.create("button", "leaflet-bar");
      btn.innerHTML = "Satellite";
      btn.title = "Satellite view";
      btn.style.cssText = "padding:4px 8px;background:#fff;border:2px solid rgba(0,0,0,0.2);border-radius:4px;cursor:pointer;font-size:12px;display:block;margin-bottom:4px;";
      btn.onclick = function () {
        if (map.hasLayer(satellite)) {
          map.removeLayer(satellite);
          map.addLayer(street);
          btn.innerHTML = "Satellite";
        } else {
          map.removeLayer(street);
          map.addLayer(satellite);
          btn.innerHTML = "Map";
        }
      };
      return btn;
    };
    satCtrl.addTo(map);

    // Pop out
    if (mapUrl && mapUrl !== "None") {
      var popCtrl = L.control({ position: "topright" });
      popCtrl.onAdd = function () {
        var a = L.DomUtil.create("a", "leaflet-bar");
        a.href = mapUrl;
        a.target = "_blank";
        a.rel = "noopener";
        a.innerHTML = "Pop out";
        a.title = "Open in Google Maps";
        a.style.cssText = "padding:4px 8px;background:#fff;border:2px solid rgba(0,0,0,0.2);border-radius:4px;cursor:pointer;font-size:12px;display:block;text-decoration:none;color:#000;";
        return a;
      };
      popCtrl.addTo(map);
    }

    // Retry tiles once if first attempt fails
    map.on("tileerror", function () {
      setTimeout(function () {
        street = L.tileLayer(STREET_URL, { maxZoom: 19, attribution: "&copy; OpenStreetMap" });
        street.addTo(map);
      }, 2000);
    });
  }

  // Init detail map
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDetailMap);
  } else {
    initDetailMap();
  }

  // Map picker — Leaflet init after HTMX swap
  document.addEventListener("htmx:afterSwap", function (event) {
    var mc = document.getElementById("leaflet-map");
    if (mc && !mc._leaflet_map && typeof L !== "undefined") {
      var lat = parseFloat(document.getElementById("map-picker-lat").textContent) || 51.5;
      var lng = parseFloat(document.getElementById("map-picker-lng").textContent) || -0.1;
      var map = L.map(mc, { center: [lat, lng], zoom: 15, zoomControl: true });
      mc._leaflet_map = map;
      L.tileLayer(STREET_URL, { maxZoom: 19, attribution: "&copy; OpenStreetMap" }).addTo(map);
      var marker = L.marker([lat, lng], { draggable: true }).addTo(map);
      var set = function (p) {
        document.getElementById("precise_lat").value = p.lat.toFixed(6);
        document.getElementById("precise_lng").value = p.lng.toFixed(6);
        document.getElementById("map-picker-lat").textContent = p.lat.toFixed(6);
        document.getElementById("map-picker-lng").textContent = p.lng.toFixed(6);
        document.getElementById("map-picker-confirm").disabled = false;
      };
      set({ lat: lat, lng: lng });
      marker.on("dragend", function () { set(marker.getLatLng()); });
      map.on("click", function (e) { marker.setLatLng(e.latlng); set(e.latlng); });
    }
  });

  document.addEventListener("htmx:beforeSwap", function () {
    var mc = document.getElementById("leaflet-map");
    if (mc && mc._leaflet_map) { mc._leaflet_map.remove(); delete mc._leaflet_map; }
  });
})();
