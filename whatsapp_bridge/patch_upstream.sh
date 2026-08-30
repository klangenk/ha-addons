#!/bin/sh
# Patches applied to the pinned upstream checkout at build time.
#
# Upstream (lharries/whatsapp-mcp) has had no commit since 2025-07 and pins
# whatsmeow from 2025-03. That whatsmeow announces a WhatsApp Web client
# version the servers now reject outright ("Client outdated (405)"), so the
# Dockerfile pulls a current whatsmeow - and the current whatsmeow takes a
# context.Context on five calls that used to do without one.
#
# Every substitution is verified. If upstream or these call sites ever move,
# the build fails here with a readable message instead of producing a binary
# that is subtly wrong.
set -eu

FILE="${1:-main.go}"

patch_once() {
    desc="$1"
    pattern="$2"
    replacement="$3"

    before="$(md5sum "$FILE" | cut -d' ' -f1)"
    sed -i "s|${pattern}|${replacement}|" "$FILE"
    after="$(md5sum "$FILE" | cut -d' ' -f1)"

    if [ "$before" = "$after" ]; then
        echo "ERROR: patch did not apply (pattern not found): ${desc}" >&2
        exit 1
    fi
    echo "patched: ${desc}"
}

# The bridge renders the pairing QR only as half-block characters, which the
# Home Assistant log panel mangles. Print the raw code as well so it can be
# turned into a QR somewhere else.
patch_once "raw QR code in the log" \
    'qrterminal\.GenerateHalfBlock(evt\.Code, qrterminal\.L, os\.Stdout)' \
    'fmt.Println("QR_CODE_RAW:", evt.Code); qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)'

# whatsmeow API drift: these five now take a context as their first argument.
patch_once "sqlstore.New takes a context" \
    'sqlstore\.New("sqlite3"' \
    'sqlstore.New(context.Background(), "sqlite3"'

patch_once "Container.GetFirstDevice takes a context" \
    'container\.GetFirstDevice()' \
    'container.GetFirstDevice(context.Background())'

patch_once "Client.Download takes a context" \
    'client\.Download(downloader)' \
    'client.Download(context.Background(), downloader)'

patch_once "Client.GetGroupInfo takes a context" \
    'client\.GetGroupInfo(jid)' \
    'client.GetGroupInfo(context.Background(), jid)'

patch_once "ContactStore.GetContact takes a context" \
    'client\.Store\.Contacts\.GetContact(jid)' \
    'client.Store.Contacts.GetContact(context.Background(), jid)'

# Cheap syntax check - catches a substitution that produced nonsense.
gofmt -e "$FILE" > /dev/null
echo "all patches applied cleanly"
