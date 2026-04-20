/**
 * Simple static file server for the dashboard_v2 frontend.
 * Serves files from dashboard_v2/static/ on port 9001 (configurable).
 */

'use strict';

const http = require('http');
const path = require('path');
const url = require('url');
const { serveStaticFile } = require('./serve-static');

class StaticServer {
  /**
   * @param {object} opts
   * @param {string} [opts.host='127.0.0.1']
   * @param {number} [opts.port=9001]
   * @param {string} [opts.staticDir]
   */
  constructor(opts = {}) {
    this._host = opts.host || '127.0.0.1';
    this._port = opts.port || 9001;
    this._staticDir = opts.staticDir || path.resolve(__dirname, '../public');
    this._httpServer = null;
  }

  start() {
    return new Promise((resolve, reject) => {
      const httpServer = http.createServer((req, res) => {
        const pathname = url.parse(req.url).pathname;
        serveStaticFile(req, res, pathname, this._staticDir);
      });

      httpServer.listen(this._port, this._host, () => {
        this._port = httpServer.address().port;
        this._httpServer = httpServer;
        console.log(`[StaticServer] Serving ${this._staticDir} on http://${this._host}:${this._port}`);
        resolve();
      });

      httpServer.on('error', reject);
    });
  }

  async stop() {
    if (this._httpServer) {
      return new Promise((resolve) => {
        this._httpServer.close(() => resolve());
      });
    }
  }
}

module.exports = { StaticServer };
