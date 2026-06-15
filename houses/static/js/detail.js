(function () {
  "use strict";

  // Satellite toggle for the detail page Google Maps iframe
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("#map-satellite-toggle");
    if (!btn) return;
    var mapId = btn.getAttribute("data-map-id");
    var iframe = document.getElementById(mapId);
    if (!iframe) return;
    var src = iframe.src;
    if (src.indexOf("t=k") !== -1) {
      iframe.src = src.replace("&t=k", "");
      btn.textContent = "Satellite";
    } else {
      iframe.src = src + "&t=k";
      btn.textContent = "Map";
    }
  });

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
