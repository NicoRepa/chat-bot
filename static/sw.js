const CACHE_NAME = 'chatbot-pwa-v2';
const ASSETS_TO_CACHE = [
  '/',
  '/panel/',
  '/static/css/panel.css',
  '/static/manifest.json'
];

// Instalar el Service Worker
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        // Se ignoran errores de assets faltantes (ej: imágenes placeholder)
        return cache.addAll(ASSETS_TO_CACHE).catch(err => console.warn('PWA Cache warn:', err));
      })
  );
});

// Activar y limpiar caches viejos
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.filter(name => name !== CACHE_NAME)
          .map(name => caches.delete(name))
      );
    })
  );
});

// Interceptar peticiones de red
self.addEventListener('fetch', event => {
  // Evitar interceptar requests al backend (APIs o POSTs) para no romper funcionalidad
  if (event.request.method !== 'GET' || event.request.url.includes('/api/') || event.request.url.includes('/webhooks/')) {
    return;
  }
  
  event.respondWith(
    fetch(event.request).catch(() => {
      // Si falla la red, intentar buscar en caché
      return caches.match(event.request);
    })
  );
});

// Escuchar notificaciones Push Server-side
self.addEventListener('push', event => {
  if (!event.data) return;

  try {
    const pushData = event.data.json();
    
    event.waitUntil(
      self.registration.showNotification(pushData.title || 'Nuevo Mensaje', {
        body: pushData.body || 'Tienes una nueva notificación.',
        icon: '/static/images/icon-192x192.png',
        badge: '/static/images/icon-192x192.png', // Un icono monocromático idealmente
        data: pushData.url || '/panel/conversaciones/',
        vibrate: [200, 100, 200]
      })
    );
  } catch (e) {
    console.error('Error procesando evento push', e);
  }
});

// Al hacer clic en la notificación
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const targetUrl = event.notification.data;

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
      // Buscar si ya hay una pestaña abierta con nuestra app
      for (let i = 0; i < windowClients.length; i++) {
        const client = windowClients[i];
        if (client.url.includes('/panel/') && 'focus' in client) {
          // Si es la URL exacta, hacer focus
          if (client.url === new URL(targetUrl, self.location.origin).href) {
            return client.focus();
          }
          // Sino navegar y enfocar
          return client.focus().then(() => client.navigate(targetUrl));
        }
      }
      // Si no hay pestañas abiertas, abrir una nueva
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});
