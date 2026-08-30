-- Chats whose newest message is NOT from me: the cheap, deterministic prefilter.
-- julianday() is used for every time comparison because go-sqlite3 writes
-- timestamps with a timezone offset, which plain string comparison gets wrong.
WITH last_msg AS (
    SELECT m.*
    FROM messages m
    JOIN (
        SELECT chat_jid, MAX(timestamp) AS ts
        FROM messages
        GROUP BY chat_jid
    ) newest
      ON newest.chat_jid = m.chat_jid
     AND newest.ts       = m.timestamp
)
SELECT
    c.jid                                                       AS jid,
    COALESCE(NULLIF(c.name, ''), c.jid)                         AS chat_name,
    l.id                                                        AS last_message_id,
    l.timestamp                                                 AS waiting_since,
    ROUND((julianday('now') - julianday(l.timestamp)) * 24, 1)  AS hours_waiting,
    CASE WHEN l.content    <> '' THEN l.content
         WHEN l.media_type <> '' THEN '[' || l.media_type || ']'
         ELSE '[leer]' END                                      AS last_message
FROM last_msg l
JOIN chats c ON c.jid = l.chat_jid
WHERE l.is_from_me = 0
  AND c.jid <> 'status@broadcast'
  AND (:include_groups = 1 OR c.jid NOT LIKE '%@g.us')
  AND julianday(l.timestamp) < julianday('now', :grace)
  AND julianday(l.timestamp) > julianday('now', :cutoff)
ORDER BY l.timestamp ASC
