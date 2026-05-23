-- XHS multi-business account configuration.
-- Safe to run more than once. Does not publish anything.

BEGIN;

CREATE TABLE IF NOT EXISTS xhs_accounts (
  id serial PRIMARY KEY,
  business text NOT NULL UNIQUE,
  display_name text NOT NULL,
  account_label text NOT NULL DEFAULT '',
  mcp_publish_url text,
  mcp_status_url text,
  enabled boolean NOT NULL DEFAULT false,
  notes text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE xhs_drafts
  ADD COLUMN IF NOT EXISTS account_id integer REFERENCES xhs_accounts(id);

INSERT INTO xhs_accounts (business, display_name, account_label, mcp_publish_url, mcp_status_url, enabled, notes)
VALUES
  ('oaklian', 'Oaklian', 'Oaklian current XHS account', 'http://localhost:18060/api/v1/publish', 'http://172.17.0.1:18060/mcp', true, 'Existing Oaklian publishing channel'),
  ('jnono', 'JNONO', 'JNONO XHS account not connected', NULL, NULL, false, 'Connect a separate JNONO publishing channel later'),
  ('pricvo', 'Pricvo', 'Pricvo XHS account not connected', NULL, NULL, false, 'Connect a separate Pricvo publishing channel later'),
  ('recossi', 'Recossi', 'Recossi XHS account not connected', NULL, NULL, false, 'Connect a separate Recossi publishing channel later')
ON CONFLICT (business) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  account_label = EXCLUDED.account_label,
  mcp_publish_url = COALESCE(xhs_accounts.mcp_publish_url, EXCLUDED.mcp_publish_url),
  mcp_status_url = COALESCE(xhs_accounts.mcp_status_url, EXCLUDED.mcp_status_url),
  notes = EXCLUDED.notes,
  updated_at = now();

UPDATE xhs_drafts d
SET account_id = a.id
FROM xhs_accounts a
WHERE d.account_id IS NULL
  AND d.business = a.business
  AND a.enabled = true;

COMMIT;
