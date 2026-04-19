/**
 * PanelSocket — Cliente WebSocket centralizado para el panel.
 * Maneja reconexión exponencial, ping/pong y dispatch de eventos.
 */
(function (window) {
    'use strict';

    // ── Configuración ──────────────────────────────────
    var RECONNECT_MIN = 1000;   // 1s
    var RECONNECT_MAX = 30000;  // 30s
    var PING_INTERVAL = 25000;  // 25s

    // ── Clase PanelSocket ──────────────────────────────
    function PanelSocket(path) {
        this.path = path;
        this.ws = null;
        this._listeners = {};
        this._reconnectDelay = RECONNECT_MIN;
        this._pingTimer = null;
        this._closed = false;
    }

    PanelSocket.prototype.connect = function () {
        if (this._closed) return;
        var self = this;
        var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var url = proto + '//' + window.location.host + this.path;

        try {
            this.ws = new WebSocket(url);
        } catch (e) {
            console.warn('[PanelSocket] WebSocket create failed:', e);
            this._scheduleReconnect();
            return;
        }

        this.ws.onopen = function () {
            self._reconnectDelay = RECONNECT_MIN;
            self._startPing();
        };

        this.ws.onmessage = function (evt) {
            try {
                var data = JSON.parse(evt.data);
                var type = (data.type || '').replace(/\./g, '_');
                if (type === 'pong') return;
                self._dispatch(type, data);
            } catch (e) {
                console.warn('[PanelSocket] Bad message:', e);
            }
        };

        this.ws.onclose = function () {
            self._stopPing();
            if (!self._closed) self._scheduleReconnect();
        };

        this.ws.onerror = function () {
            // onclose se llama después, no duplicar lógica
        };
    };

    PanelSocket.prototype.close = function () {
        this._closed = true;
        this._stopPing();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    };

    PanelSocket.prototype.on = function (eventType, callback) {
        if (!this._listeners[eventType]) this._listeners[eventType] = [];
        this._listeners[eventType].push(callback);
        return this; // chainable
    };

    PanelSocket.prototype.off = function (eventType, callback) {
        var list = this._listeners[eventType];
        if (!list) return;
        if (callback) {
            this._listeners[eventType] = list.filter(function (fn) { return fn !== callback; });
        } else {
            delete this._listeners[eventType];
        }
    };

    PanelSocket.prototype._dispatch = function (type, data) {
        var list = this._listeners[type] || [];
        for (var i = 0; i < list.length; i++) {
            try {
                list[i](data);
            } catch (e) {
                console.error('[PanelSocket] Listener error:', e);
            }
        }
        // Wildcard listener
        var all = this._listeners['*'] || [];
        for (var j = 0; j < all.length; j++) {
            try {
                all[j](type, data);
            } catch (e) {
                console.error('[PanelSocket] Wildcard listener error:', e);
            }
        }
    };

    PanelSocket.prototype._startPing = function () {
        var self = this;
        this._stopPing();
        this._pingTimer = setInterval(function () {
            if (self.ws && self.ws.readyState === WebSocket.OPEN) {
                self.ws.send(JSON.stringify({ action: 'ping' }));
            }
        }, PING_INTERVAL);
    };

    PanelSocket.prototype._stopPing = function () {
        if (this._pingTimer) {
            clearInterval(this._pingTimer);
            this._pingTimer = null;
        }
    };

    PanelSocket.prototype._scheduleReconnect = function () {
        var self = this;
        var delay = this._reconnectDelay;
        this._reconnectDelay = Math.min(this._reconnectDelay * 2, RECONNECT_MAX);
        setTimeout(function () { self.connect(); }, delay);
    };


    // ── API pública ────────────────────────────────────
    var _sockets = {};

    window.PanelRealtime = {
        /**
         * Suscribirse al inbox del negocio (recibe todos los updates).
         * @returns {PanelSocket}
         */
        subscribeInbox: function () {
            if (_sockets.inbox) return _sockets.inbox;
            var sock = new PanelSocket('/ws/panel/inbox/');
            sock.connect();
            _sockets.inbox = sock;
            return sock;
        },

        /**
         * Suscribirse a una conversación específica.
         * @param {string} conversationId
         * @returns {PanelSocket}
         */
        subscribeConversation: function (conversationId) {
            var key = 'conv_' + conversationId;
            if (_sockets[key]) return _sockets[key];
            var sock = new PanelSocket('/ws/panel/conversations/' + conversationId + '/');
            sock.connect();
            _sockets[key] = sock;
            return sock;
        },

        /**
         * Cerrar una suscripción específica.
         */
        unsubscribe: function (key) {
            if (_sockets[key]) {
                _sockets[key].close();
                delete _sockets[key];
            }
        },

        /**
         * Cerrar todas las suscripciones.
         */
        closeAll: function () {
            Object.keys(_sockets).forEach(function (k) {
                _sockets[k].close();
            });
            _sockets = {};
        }
    };

})(window);
