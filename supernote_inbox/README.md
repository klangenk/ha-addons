# Supernote Convert - Home Assistant add-on

Turns a Supernote `.note` notebook into its pages: the recognised text the
device already embedded, or a PNG for pages it could not read. One endpoint,
no state, no credentials.

It exists so handwritten notes can enter the existing capture pipeline
(Supernote → Nextcloud → n8n → Inbox → Obsidian) as ordinary text captures.

## What it deliberately does not do

**It does not watch the Nextcloud folder.** n8n already polls Nextcloud and
already decides what is new; a second poller would mean two systems answering
that question and disagreeing about it. n8n downloads the notebook, posts it
here, and writes the pages back.

**It runs no model.** The text comes from the Supernote's own handwriting
recognition, stored inside the `.note` file by firmware. Parsing it is CPU
work measured in milliseconds, which is why this lives on the Pi and not on
the GPU machine - handwriting never needs the tower PC to be awake.

The catch: notebooks must be created as **Real-Time Recognition** notes
(Supernote: new note → note type). An ordinary note contains no text at all,
and the response says so via `realtime_recognition: false`.

## API

```
GET  /health                    open, so a health check needs no secret
POST /convert?images=true       body: the raw .note file
                                needs Authorization: Bearer <api_token>
```

`images=false` skips PNG rendering and returns sketch pages as metadata only -
useful when the caller just wants to know whether anything changed.

Response:

```json
{
  "realtime_recognition": true,
  "total_pages": 12,
  "pages": [
    { "page": 1, "page_id": "a1b2...", "kind": "text",
      "digest": "9f86d081884c7d65", "text": "Idee: Konverter aufs Pi ..." },
    { "page": 2, "page_id": "c3d4...", "kind": "sketch",
      "digest": "2c26b46b68ffc68f", "png_base64": "iVBORw0KGgo..." },
    { "page": 3, "page_id": "e5f6...", "kind": "empty", "digest": "" }
  ]
}
```

| Field | Meaning |
|---|---|
| `page` | 1-based position in the notebook |
| `page_id` | the Supernote's own page id - **stable across edits and reordering** |
| `kind` | `text`, `sketch` (ink but no recognised text) or `empty` |
| `digest` | short hash of the text, or of the raw ink for sketches |

`page_id` plus `digest` is the pair that makes deduplication possible: the id
says *which* page, the digest says *whether it changed*. A notebook is a living
file - it changes with every stroke - so without that pair every sync would
re-emit every page.

Errors: `401` wrong or missing token, `400` empty body, `413` over 256 MB,
`422` not a readable `.note` file.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → add
   `https://github.com/klangenk/ha-addons`.
2. Install **Supernote Convert**. The build only installs wheels - about a
   minute on a Pi 4, unlike the WhatsApp add-on next door.
3. Configuration tab → set `api_token` to a long random string. Save, start.
4. Check the Log tab for `listening on port 8099`.

Updates need a bumped `version:` in `config.yaml`; without one the Supervisor
does not notice a push.

## Reaching it from n8n

Both add-ons sit on the same Docker network, so the port does not need to be
published. The hostname is the full slug with underscores turned into hyphens,
and the slug carries a prefix for where the add-on came from:

| installed from | slug | hostname |
|---|---|---|
| this repository | `<repo-hash>_supernote_inbox` | `<repo-hash>-supernote-inbox` |
| a folder in `/addons` | `local_supernote_inbox` | `local-supernote-inbox` |

Read the actual one off any Supervisor log line for the add-on, or off the URL
of its page in the UI.

For a quick check from a browser, set a free host port under
**Configuration → Network** instead.

## The n8n side

A workflow of its own, separate from the Inbox pipeline - different source
folder, different cadence:

1. **Schedule Trigger** - every few minutes.
2. **Nextcloud → List files** on `/Supernote/Note`, the folder the device
   syncs into. Keep notebooks flat in there: the node does not recurse.
3. **Filter**: `modified` older than ~10 minutes. A notebook that was touched
   seconds ago is probably still being written in, and converting it now would
   capture half a sentence and emit the rest again later as a revision.
   **This has to come before the dedupe** - deduplicating first would record
   the version that was then filtered out, and that notebook would never be
   converted.
4. **Remove Duplicates** (*seen in previous executions*), key `path|etag` -
   so a notebook is only downloaded and converted when it actually changed.
5. **Loop Over Items**, batch size 1. Everything below runs per notebook,
   which is what makes step 9 unambiguous.
6. **Nextcloud → Download**, then **HTTP Request** `POST` to
   `http://<addon-hostname>:8099/convert`, body = the binary field, header
   auth credential with the bearer token.
7. **Split Out** on `pages`.
8. **Remove Duplicates** (*seen in previous executions*), key
   `page_id|digest` - this is the one that keeps the Inbox clean. New page or
   edited page passes, everything else stops here.
9. **Switch** on `kind`:
   * `text` → **Sort** by `page` → **Aggregate** → a **Code** node joins the
     pages into one document (see below) → **Convert to File** →
     **Nextcloud → Upload** into `/Inbox`.
   * `sketch` → straight to **Move Base64 String to File** → **Upload**, one
     PNG per page.

### Why the pages are joined again

A page break in a notebook is where the paper ended, not where the thought
ended - a sentence running across two pages would otherwise become two
captures, neither of which makes sense alone. So the pages travel through the
workflow individually, because that is what deduplication needs, and are put
back together into one capture right before upload:

```javascript
const meta  = $('Loop Over Items').first().json;
const pages = $input.first().json.data;          // Aggregate output
const stem  = meta.path.split('/').pop()
  .replace(/\.note$/i, '').replace(/[^\p{L}\p{N}.-]+/gu, '-');
const ts    = DateTime.fromISO(meta.lastModified)
  .setZone('Europe/Berlin').toFormat('yyyy-MM-dd_HH-mm-ss');
const pad   = (n) => String(n).padStart(2, '0');
const range = pages.length === 1
  ? `S${pad(pages[0].page)}`
  : `S${pad(pages[0].page)}-${pad(pages[pages.length - 1].page)}`;

const header = [`# ${stem}`, `Quelle: Supernote, ${meta.path}`, ''].join('\n');
const body = pages
  .map((p) => [`--- Seite ${p.page} ---`, '', p.text].join('\n'))
  .join('\n\n');

return { json: {
  filename: `${ts}_Supernote_${stem}_${range}.txt`,
  content: `${header}\n${body}\n`,
} };
```

One file per notebook per run, containing exactly the pages that are new. Use
the notebook's mtime for the timestamp, not the current time: it is the
closest thing to when the page was written, and it keeps filenames stable
across runs.

Guard the branch with a filter on a non-empty `data` before uploading -
a run where every page was already known aggregates to nothing.

The notebooks themselves are never moved or renamed. They belong to the
device's sync, and archiving them the way the Inbox pipeline archives captures
would fight it - the device would simply upload them again.

Sketch pages land in the Inbox as PNG and wait there for the pipeline's image
branch, exactly like photos from the phone do today.
