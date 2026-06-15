(function () {
  "use strict";

  var SATELLITE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
  var STREET_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";

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

    // Satellite toggle control
    var satelliteControl = L.control({ position: "topright" });
    satelliteControl.onAdd = function () {
      var btn = L.DomUtil.create("button", "leaflet-control leaflet-bar map-control-btn");
      btn.innerHTML = "Satellite";
      btn.title = "Toggle satellite view";
      btn.style.padding = "4px 8px";
      btn.style.background = "#fff";
      btn.style.border = "2px solid rgba(0,0,0,0.2)";
      btn.style.borderRadius = "4px";
      btn.style.cursor = "pointer";
      btn.style.fontSize = "12px";
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
    satelliteControl.addTo(map);

    // Pop out control
    if (mapUrl) {
      var popOutControl = L.control({ position: "topright" });
      popOutControl.onAdd = function () {
        var a = L.DomUtil.create("a", "leaflet-control leaflet-bar map-control-btn");
        a.href = mapUrl;
        a.target = "_blank";
        a.rel = "noopener";
        a.innerHTML = "Pop out";
        a.title = "Open in Google Maps";
        a.style.padding = "4px 8px";
        a.style.background = "#fff";
        a.style.border = "2px solid rgba(0,0,0,0.2)";
        a.style.borderRadius = "4px";
        a.style.cursor = "pointer";
        a.style.fontSize = "12px";
        a.style.display = "inline-block";
        a.style.marginTop = "4px";
        a.style.textDecoration = "none";
        a.style.color = "#000";
        return a;
      };
      popOutControl.addTo(map);
    }
  }

  // Init detail map on page load
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDetailMap);
  } else {
    initDetailMap();
  }

  // Map picker — Leaflet init after HTMX swap
  document.addEventListener("htmx:afterSwap", function (event) {
    var mapContainer = document.getElementById("leaflet-map");
    if (mapContainer && !mapContainer._leaflet_map) {
      if (typeof L !== "undefined") {
        var lat = parseFloat(document.getElementById("map-picker-lat").textContent) || 51.5;
        var lng = parseFloat(document.getElementById("map-picker-lng").textContent) || -0.1;
        var map = L.map(mapContainer, {
          center: [lat, lng],
          zoom: 15,
          zoomControl: true,
        });
        mapContainer._leaflet_map = map;

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "&copy; OpenStreetMap contributors",
        }).addTo(map);

        var marker = L.marker([lat, lng], { draggable: true }).addTo(map);
        document.getElementById("precise_lat").value = lat;
        document.getElementById("precise_lng").value = lng;
        document.getElementById("map-picker-confirm").disabled = false;
        document.getElementById("map-picker-lat").textContent = lat.toFixed(6);
        document.getElementById("map-picker-lng").textContent = lng.toFixed(6);

        marker.on("dragend", function () {
          var pos = marker.getLatLng();
          document.getElementById("precise_lat").value = pos.lat.toFixed(6);
          document.getElementById("precise_lng").value = pos.lng.toFixed(6);
          document.getElementById("map-picker-lat").textContent = pos.lat.toFixed(6);
          document.getElementById("map-picker-lng").textContent = pos.lng.toFixed(6);
        });

        map.on("click", function (e) {
          marker.setLatLng(e.latlng);
          document.getElementById("precise_lat").value = e.latlng.lat.toFixed(6);
          document.getElementById("precise_lng").value = e.latlng.lng.toFixed(6);
          document.getElementById("map-picker-lat").textContent = e.latlng.lat.toFixed(6);
          document.getElementById("map-picker-lng").textContent = e.latlng.lng.toFixed(6);
          document.getElementById("map-picker-confirm").disabled = false;
        });
      }
    }
  });

  document.addEventListener("htmx:beforeSwap", function (event) {
    var mapContainer = document.getElementById("leaflet-map");
    if (mapContainer && mapContainer._leaflet_map) {
      mapContainer._leaflet_map.remove();
      delete mapContainer._leaflet_map;
    }
  });
})();
