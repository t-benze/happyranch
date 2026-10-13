CREATE TABLE identity_name_schema (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  version INTEGER NOT NULL CHECK(version=1),
  org_slug TEXT NOT NULL CHECK(length(org_slug)>0)
);
CREATE TABLE identity_name_owners (
  kind TEXT NOT NULL CHECK(kind IN ('agent','founder')),
  canonical_id TEXT NOT NULL CHECK(length(canonical_id)>0),
  lifecycle TEXT NOT NULL CHECK(lifecycle IN ('active','pending','terminated','absent','founder')),
  current_label TEXT,
  revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
  PRIMARY KEY(kind,canonical_id),
  CHECK((kind='founder' AND canonical_id='founder' AND lifecycle='founder')
     OR (kind='agent' AND lifecycle!='founder')),
  CHECK(current_label IS NULL OR
    (current_label=canonical_id OR
     (length(current_label) BETWEEN 1 AND 64
      AND current_label NOT GLOB '*[^A-Za-z0-9_-]*'
      AND substr(current_label,1,1) GLOB '[A-Za-z0-9]'))),
  CHECK(lifecycle='absent' OR current_label IS NOT NULL)
);
CREATE TABLE identity_name_claims (
  normalized_name TEXT PRIMARY KEY NOT NULL CHECK(length(normalized_name)>0),
  owner_kind TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  id_reserved INTEGER NOT NULL CHECK(id_reserved IN (0,1)),
  permanent INTEGER NOT NULL CHECK(permanent IN (0,1)),
  FOREIGN KEY(owner_kind,owner_id) REFERENCES identity_name_owners(kind,canonical_id),
  CHECK(id_reserved=1 OR permanent=1),
  CHECK(normalized_name=lower(normalized_name)
    AND (normalized_name=lower(owner_id) OR
      (length(normalized_name) BETWEEN 1 AND 64
       AND normalized_name NOT GLOB '*[^a-z0-9_-]*'
       AND substr(normalized_name,1,1) GLOB '[a-z0-9]')))
);
