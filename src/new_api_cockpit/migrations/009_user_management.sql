-- Application operations only. No source credentials or raw upstream responses.
CREATE TABLE user_management_operations (
 id uuid PRIMARY KEY, operator_id bigint NOT NULL, operator_name text NOT NULL,
 action text NOT NULL, parameters jsonb NOT NULL DEFAULT '{}',
 state text NOT NULL DEFAULT 'preview' CHECK(state IN ('preview','running','completed','partial','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 confirmed_at timestamptz, finished_at timestamptz
);
CREATE INDEX user_management_operations_created ON user_management_operations(created_at DESC);
CREATE TABLE user_management_operation_items (
 operation_id uuid NOT NULL REFERENCES user_management_operations(id), target_type text NOT NULL,
 target_id bigint NOT NULL, user_id bigint NOT NULL, before_data jsonb NOT NULL DEFAULT '{}',
 after_data jsonb NOT NULL DEFAULT '{}',
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sending','success','failed','unknown','conflict')),
 message text NOT NULL DEFAULT '', started_at timestamptz, finished_at timestamptz,
 PRIMARY KEY(operation_id,target_id)
);
