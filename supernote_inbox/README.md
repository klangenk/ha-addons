# Supernote Convert - Home Assistant add-on

Turns a Supernote `.note` notebook into its pages: the recognised text the
device already embedded, or a PNG for pages it could not read. No state, no
credentials, no scheduler.

It exists so handwritten notes become readable to everything downstream of
Nextcloud - first as a plain text mirror of the notebook folder, later, if
wanted, as individual captures in the existing pipeline.

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
GET  /health
POST /convert?images=true       body: the raw .note file -> JSON, one entry per page
POST /convert/text              body: the raw .note file -> plain text
```

`/convert/text` returns the whole notebook as one document. It is what the
folder mirror below uses, and what to reach for when looking at a notebook by
hand:

```bash
curl --data-binary @Ideen.note http://ee64ca8a-supernote-inbox:8099/convert/text
```

`?page_headers=false` drops the `--- Seite N ---` lines.

`/convert` returns the same content split per page, each entry carrying a
`page_id` and a `digest`. Use it when the caller needs to tell which pages
changed - that is the difference between mirroring a notebook and filing its
pages, see the two workflows below.

**Authentication is optional and off by default.** With the port unpublished,
the only clients that can reach the service are other add-ons and Home
Assistant itself - and there is nothing here to reach: no data at rest, no
credentials, no write path, and a response that contains only what the caller
just uploaded. That is what separates it from the WhatsApp add-on next door,
whose API serves the entire chat history and is therefore never open.

Set `api_token` if you publish the port under **Configuration → Network** for
debugging; requests then need `Authorization: Bearer <api_token>`. The startup
log line says which mode is active.

`images=false` skips PNG rendering and returns sketch pages as metadata only -
useful for getting the text path working first, before letting a Pi render
1404x1872 pages.

> **If you run with `images=false`, filter `kind == "sketch"` out before the
> page dedupe.** Otherwise those pages are recorded as seen while producing
> no file at all, and switching to `images=true` later will not bring them
> back - the dedupe already knows them. Drop the filter when you flip the
> flag, and every sketch page shows up as new.

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
| `text` | on `kind: text` only |
| `png_base64` | on `kind: sketch` only, and only with `images=true` - the key is **absent**, not null, otherwise |

`page_id` plus `digest` is the pair that makes deduplication possible: the id
says *which* page, the digest says *whether it changed*. A notebook is a living
file - it changes with every stroke - so without that pair every sync would
re-emit every page.

Errors: `400` empty body, `413` over 256 MB, `422` not a readable `.note`
file, and `401` for a bad token when `api_token` is set.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ → **Repositories** → add
   `https://github.com/klangenk/ha-addons`.
2. Install **Supernote Convert**. The build only installs wheels - about a
   minute on a Pi 4, unlike the WhatsApp add-on next door.
3. Start it. No configuration needed - `api_token` stays empty unless you
   publish the port.
4. Check the Log tab for `listening on port 8099, open (no api_token set)`.

Updates need a bumped `version:` in `config.yaml`; without one the Supervisor
does not notice a push.

## Reaching it from n8n

Both add-ons sit on the same Docker network, so the port does not need to be
published. The hostname is the full slug with underscores turned into hyphens,
and the slug carries a prefix for where the add-on came from:

| installed from | slug | hostname |
|---|---|---|
| this repository | `ee64ca8a_supernote_inbox` | **`ee64ca8a-supernote-inbox`** |
| a folder in `/addons` | `local_supernote_inbox` | `local-supernote-inbox` |

`ee64ca8a` identifies the *repository*, not the add-on, so it is the same
prefix whatsapp_bridge carries. It is derived from the repository URL, so
re-adding the repo under a different form of the URL would change it - read
the actual value off any Supervisor log line for the add-on, or off the URL of
its page in the UI (`/hassio/addon/ee64ca8a_supernote_inbox/info`).

For a quick check from a browser, set a free host port under
**Configuration → Network** instead.

## The n8n side, first use case: mirror the folder

The simplest thing that is useful: one `.note` in, one `.txt` out, overwritten
whenever the notebook changes. Nothing accumulates, so there is nothing to
deduplicate - same input, same output file.

```
/Supernote/Note/Ideen.note   ->   /Supernote/Text/Ideen.txt
```

| # | Node | Configuration |
|---|---|---|
| 1 | Schedule Trigger | every 5 minutes |
| 2 | Nextcloud → Folder: List | `Supernote/Note` |
| 3 | Nextcloud → Folder: List | `Supernote/Text`, **Always Output Data on** - the folder is empty on the first run |
| 4 | Code | keep only notebooks whose `.txt` is missing or older, see below |
| 5 | Nextcloud → Download → HTTP Request | `POST /convert/text` |
| 6 | Convert to File → Nextcloud → Upload | `Supernote/Text/<stem>.txt`, overwrite on |

### Deciding what is new

Compare the two folders rather than remembering what was already done. The
target folder is the state - `make`'s rule, target older than source means
rebuild:

```javascript
const done = new Map(
  $('List text folder').all().map((i) => {
    const stem = i.json.path.split('/').pop().replace(/\.txt$/i, '');
    return [stem, new Date(i.json.lastModified).getTime()];
  }),
);

return $('List notebooks').all()
  .filter((i) => i.json.path.toLowerCase().endsWith('.note'))
  .filter((i) => {
    const stem = i.json.path.split('/').pop().replace(/\.note$/i, '');
    const txt = done.get(stem);
    return txt === undefined || new Date(i.json.lastModified).getTime() > txt;
  });
```

This is self-healing, which a remembered-state approach is not. If an upload
fails, the `.txt` stays missing and the next run picks the notebook up again.
Delete a `.txt` and it regenerates. To reconvert everything, empty the target
folder - there is no state anywhere else to reset.

A **Remove Duplicates** node (*seen in previous executions*, key `path|eTag`)
is the one-node version and works, but it marks a notebook as seen *before*
the upload happens: one failure downstream and that notebook is recorded as
done and never touched again, silently, until you edit it once more.

No settle filter is needed here, unlike in the per-page workflow below. A
notebook still being written in just gets converted a few times for nothing -
the mirror overwrites, so there is no half-sentence capture to regret.

Put the output wherever the reader lives. Outside the Obsidian vault keeps raw
conversions from sitting among curated notes without frontmatter; inside its
`Inbox/` hands them to the vault's librarian instead.

## Later: per-page captures with routing

The mirror gives you a readable copy of a notebook. What it does not do is
*file* anything: a page holding a recipe does not end up in `Recipes/`, a
todo does not reach Microsoft To-Do. That needs the pages to enter the capture
pipeline individually, which in turn needs deduplication - otherwise every
sync re-delivers pages that are already filed, and the difference has to be
worked out by reading.

That is what `/convert` and the `page_id|digest` pair are for. A workflow of
its own, separate from the Inbox pipeline - different source folder, different
cadence:

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
   `http://ee64ca8a-supernote-inbox:8099/convert`, body = the binary field. No
   credential needed unless you set `api_token`.
7. **Split Out** on `pages`. While running with `images=false`, add a
   **Filter** `{{ $json.kind !== 'sketch' }}` here - see the warning under
   *API*.
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
