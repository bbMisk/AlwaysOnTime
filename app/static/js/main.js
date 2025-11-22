// Theme Management
function toggleTheme() {
  const html = document.documentElement;
  const currentTheme = html.getAttribute('data-theme');
  const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

  html.setAttribute('data-theme', newTheme);
  localStorage.setItem('theme', newTheme);
  updateThemeIcon(newTheme);
}

function updateThemeIcon(theme) {
  const icon = document.getElementById('theme-icon');
  const text = document.getElementById('theme-text');
  if (theme === 'dark') {
    icon.textContent = '🌙';
    text.textContent = 'Dark';
  } else {
    icon.textContent = '☀️';
    text.textContent = 'Light';
  }
}

// Initialize Theme
const savedTheme = localStorage.getItem('theme') || (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
document.documentElement.setAttribute('data-theme', savedTheme);
document.addEventListener('DOMContentLoaded', () => updateThemeIcon(savedTheme));

// Map Initialization (Leaflet)
let map;
let markers = [];

function initMap() {
  if (!document.getElementById('map')) return;

  map = L.map('map').setView([40.7128, -74.0060], 13);

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '© OpenStreetMap contributors'
  }).addTo(map);

  // Load stations
  fetch('/api/stations')
    .then(res => res.json())
    .then(data => {
      data.stations.forEach(station => {
        const marker = L.marker([station.latitude, station.longitude], {
          icon: L.icon({
            iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-blue.png',
            iconSize: [25, 41],
            iconAnchor: [12, 41]
          })
        })
          .bindPopup(`<b>${station.name}</b><br>${station.lines.join(', ')}<br><a href="/station/${station.id}">View Details →</a>`);
        marker.addTo(map);
        markers.push(marker);
      });
    });

  // Start vehicle updates
  updateVehiclePositions();
  setInterval(updateVehiclePositions, 15000);
}

// Vehicle Tracking
let vehicleMarkers = {};
const lineColors = {
  '1': '#EE352E', '2': '#EE352E', '3': '#EE352E',
  '4': '#00933C', '5': '#00933C', '6': '#00933C',
  '7': '#B933AD', 'S': '#808183',
  'A': '#0039A6', 'C': '#0039A6', 'E': '#0039A6',
  'B': '#FF6319', 'D': '#FF6319', 'F': '#FF6319', 'M': '#FF6319',
  'G': '#6CBE45', 'J': '#996633', 'Z': '#996633',
  'L': '#A7A9AC', 'N': '#FCCC0A', 'Q': '#FCCC0A', 'R': '#FCCC0A', 'W': '#FCCC0A'
};

function getLineColor(routeId) {
  if (!routeId) return '#808183';
  const line = routeId.charAt(0);
  return lineColors[line] || '#808183';
}

function createVehicleIcon(routeId) {
  const color = getLineColor(routeId);
  return L.divIcon({
    className: 'vehicle-marker',
    html: `<div style="background-color: ${color}; width: 12px; height: 12px; border-radius: 50%; border: 2px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5);"></div>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6]
  });
}

function updateVehiclePositions() {
  if (!map) return;

  fetch('/api/vehicles')
    .then(response => response.json())
    .then(data => {
      // Remove old vehicle markers
      Object.keys(vehicleMarkers).forEach(id => {
        map.removeLayer(vehicleMarkers[id]);
        delete vehicleMarkers[id];
      });

      // Add new vehicle markers
      data.vehicles.forEach(vehicle => {
        if (vehicle.latitude && vehicle.longitude) {
          const icon = createVehicleIcon(vehicle.route_id);
          const marker = L.marker([vehicle.latitude, vehicle.longitude], { icon: icon }).addTo(map);
          const routeInfo = vehicle.route_id ? `Line: ${vehicle.route_id}` : 'Vehicle';
          marker.bindPopup(`<strong>${routeInfo}</strong><br>Live position`);
          vehicleMarkers[vehicle.id] = marker;
        }
      });
    })
    .catch(error => console.error('Error loading vehicles:', error));
}

// HTMX Events
document.body.addEventListener('htmx:afterSwap', function (evt) {
  // Re-initialize components if needed after HTMX swap
});

// Initialize everything on load
document.addEventListener('DOMContentLoaded', () => {
  initMap();
});
