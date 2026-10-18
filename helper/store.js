// Ducky’s Clip Organizer, the desktop app on this computer that holds the clip library.
const APP = 'http://127.0.0.1:47833';

async function app(path, body) {
  const response = await fetch(APP + path, body === undefined ? {headers: {'X-Clip-Organizer': '1'}} : {
    method: 'POST', headers: {'X-Clip-Organizer': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!response.ok) throw new Error(`Ducky’s Clip Organizer answered ${response.status}`);
  return path.startsWith('/file/') ? response : response.json();
}
