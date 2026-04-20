/**
 * Shared static file serving utility.
 * Used by both DashboardServer and StaticServer.
 */

'use strict';

const path = require('path');
const fs = require('fs');
const fsp = fs.promises;

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

/**
 * Serve a static file from the given directory.
 * Uses async file I/O to avoid blocking the event loop.
 *
 * @param {http.IncomingMessage} req
 * @param {http.ServerResponse} res
 * @param {string} pathname - URL pathname (e.g. '/index.html')
 * @param {string} staticDir - absolute path to the static directory
 */
async function serveStaticFile(req, res, pathname, staticDir) {
  if (pathname === '/' || pathname === '/index.html') {
    pathname = '/index.html';
  }

  const safePath = pathname.replace(/^\/+/, '');
  const filePath = path.resolve(staticDir, safePath);

  // Security: ensure file is within static dir
  if (!filePath.startsWith(path.resolve(staticDir))) {
    res.writeHead(403, { 'Content-Type': 'text/plain' });
    res.end('Forbidden');
    return;
  }

  try {
    const stat = await fsp.stat(filePath);
    if (!stat.isFile()) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not Found');
      return;
    }

    const ext = path.extname(filePath);
    const contentType = CONTENT_TYPES[ext] || 'application/octet-stream';

    res.writeHead(200, { 'Content-Type': contentType });
    const stream = fs.createReadStream(filePath);
    stream.pipe(res);
    stream.on('error', () => {
      if (!res.headersSent) {
        res.writeHead(500, { 'Content-Type': 'text/plain' });
      }
      res.end('Internal Server Error');
    });
  } catch (e) {
    if (e.code === 'ENOENT') {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not Found');
    } else {
      res.writeHead(500, { 'Content-Type': 'text/plain' });
      res.end('Internal Server Error');
    }
  }
}

module.exports = { serveStaticFile, CONTENT_TYPES };
