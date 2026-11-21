/** Public browser endpoints only; credentials and private storage belong to the server. */
export function resolveRepositoryUrl(value, base, label = 'Repository URL') {
  if (typeof value !== 'string') throw new TypeError(`${label} must be a URL string.`);
  if (/[\s\u0000-\u001f\u007f\\<>"'`]/.test(value)) {
    throw new TypeError(`${label} contains an unsupported URL character.`);
  }
  let url;
  try { url = new URL(value || '.', base); }
  catch { throw new TypeError(`${label} must be an absolute HTTP(S) URL or a path relative to config.json.`); }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) {
    throw new TypeError(`${label} must use HTTPS, or HTTP(S) on a loopback host.`);
  }
  // URL.hash/search are empty for a bare '#'/'?'; those delimiters would still
  // break API path appending, so reject them in the serialized URL as well.
  if (url.username || url.password || url.href.includes('#') || url.href.includes('?')) {
    throw new TypeError(`${label} cannot contain embedded credentials, a fragment, or a query.`);
  }
  return url.href;
}
