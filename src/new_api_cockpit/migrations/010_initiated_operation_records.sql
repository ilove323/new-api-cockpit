-- Internal execution plans are separate from the journal of initiated requests.
-- Apply while the previous application is stopped. No New API tables are touched.
CREATE TABLE user_management_operation_targets (
    operation_id uuid NOT NULL REFERENCES user_management_operations(id) ON DELETE CASCADE,
    target_type text NOT NULL, target_id bigint NOT NULL, user_id bigint NOT NULL,
    before_data jsonb NOT NULL DEFAULT '{}', after_data jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (operation_id, target_id)
);
CREATE TABLE quota_schedule_run_targets (
    run_id bigint NOT NULL REFERENCES quota_schedule_runs(id) ON DELETE CASCADE,
    user_id bigint NOT NULL, username text NOT NULL, display_name text NOT NULL DEFAULT '',
    group_name text NOT NULL, operation text NOT NULL CHECK (operation IN ('add','subtract')),
    amount_units bigint NOT NULL CHECK (amount_units > 0),
    PRIMARY KEY (run_id, user_id)
);

-- Preserve anything with an initiation timestamp conservatively as uncertain.
UPDATE user_management_operation_items SET state='unknown'
WHERE state='pending' AND started_at IS NOT NULL;
UPDATE quota_schedule_run_items SET status='unknown'
WHERE status IN ('pending','skipped') AND request_started_at IS NOT NULL;
UPDATE user_management_operations o SET state='partial', finished_at=now()
WHERE EXISTS (SELECT 1 FROM user_management_operation_items i
              WHERE i.operation_id=o.id AND i.state='pending')
  AND NOT EXISTS (SELECT 1 FROM user_management_operation_items i
                  WHERE i.operation_id=o.id AND i.state='sending');
DELETE FROM user_management_operation_items WHERE state='pending';
DELETE FROM quota_schedule_run_items WHERE status IN ('pending','skipped');
DELETE FROM user_management_operations o
WHERE NOT EXISTS (SELECT 1 FROM user_management_operation_items i WHERE i.operation_id=o.id);
DELETE FROM quota_schedule_runs r
WHERE NOT EXISTS (SELECT 1 FROM quota_schedule_run_items i WHERE i.run_id=r.id);

ALTER TABLE user_management_operation_items DROP CONSTRAINT user_management_operation_items_state_check;
ALTER TABLE user_management_operation_items ALTER COLUMN state SET DEFAULT 'sending';
ALTER TABLE user_management_operation_items ADD CHECK (state IN ('sending','success','failed','unknown','conflict'));
ALTER TABLE quota_schedule_run_items DROP CONSTRAINT quota_schedule_run_items_status_check;
ALTER TABLE quota_schedule_run_items ADD CHECK (status IN ('sending','success','failed','unknown'));
ALTER TABLE quota_schedule_runs DROP COLUMN skipped_count;
UPDATE quota_schedule_runs r SET
    total_count=(SELECT count(*) FROM quota_schedule_run_items i WHERE i.run_id=r.id),
    success_count=(SELECT count(*) FROM quota_schedule_run_items i WHERE i.run_id=r.id AND i.status='success'),
    failed_count=(SELECT count(*) FROM quota_schedule_run_items i WHERE i.run_id=r.id AND i.status='failed'),
    unknown_count=(SELECT count(*) FROM quota_schedule_run_items i WHERE i.run_id=r.id AND i.status='unknown');
