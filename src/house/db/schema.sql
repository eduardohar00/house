-- Esquema inicial de House (SQLite). Fase 0: contrato de datos; la Fase 1 lo cifra y lo migra.
PRAGMA foreign_keys = ON;

CREATE TABLE person (
  id            INTEGER PRIMARY KEY,
  display_name  TEXT NOT NULL,
  birth_date    TEXT NOT NULL,                       -- AAAA-MM-DD; nunca sale de la Mac
  sex_at_birth  TEXT NOT NULL CHECK (sex_at_birth IN ('F','M')),
  is_admin      INTEGER NOT NULL DEFAULT 0,
  has_login     INTEGER NOT NULL DEFAULT 1,          -- 0 = solo lo administra el admin
  pin_hash      TEXT,                                -- hash con sal (argon2/scrypt); nunca el PIN
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE document (
  id            INTEGER PRIMARY KEY,
  person_id     INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  doc_type      TEXT NOT NULL CHECK (doc_type IN ('laboratorio','imagen','receta','nota_clinica','vacuna','otro')),
  title         TEXT NOT NULL,
  source_name   TEXT,                                -- laboratorio u hospital
  collected_on  TEXT,                                -- fecha del estudio
  file_path     TEXT NOT NULL,                       -- original cifrado, fuera de la base
  file_sha256   TEXT NOT NULL,
  review_state  TEXT NOT NULL DEFAULT 'pendiente' CHECK (review_state IN ('pendiente','revisada','descartada')),
  uploaded_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (person_id, file_sha256)                    -- detecta documentos duplicados
);

CREATE TABLE observation (
  id            INTEGER PRIMARY KEY,
  person_id     INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  document_id   INTEGER NOT NULL REFERENCES document(id) ON DELETE CASCADE,
  analyte_key   TEXT NOT NULL,                       -- clave canónica (ver normalize/terminology.py)
  loinc         TEXT,
  printed_name  TEXT NOT NULL,                       -- tal como aparecía en el documento
  value_num     REAL NOT NULL,                       -- en unidad canónica
  unit          TEXT NOT NULL,
  value_printed TEXT NOT NULL,                       -- lo impreso, para auditar conversiones
  unit_printed  TEXT,
  ref_low       REAL,
  ref_high      REAL,
  collected_on  TEXT NOT NULL,
  source_page   INTEGER,
  source_region TEXT,                                -- coordenadas para resaltar en el original
  confirmed_by  INTEGER REFERENCES person(id),       -- quién confirmó la revisión
  confirmed_at  TEXT
);
CREATE INDEX idx_obs_series ON observation (person_id, analyte_key, collected_on);

CREATE TABLE medication (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  name TEXT NOT NULL, dose TEXT, reason TEXT, since_year TEXT, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE problem (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  name TEXT NOT NULL, status TEXT NOT NULL, since_year TEXT
);
CREATE TABLE allergy (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  substance TEXT NOT NULL, reaction TEXT
);
CREATE TABLE family_history (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  relative TEXT NOT NULL, condition TEXT NOT NULL
);
CREATE TABLE procedure_history (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  name TEXT NOT NULL, year TEXT
);
CREATE TABLE imaging_study (
  id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  document_id INTEGER REFERENCES document(id) ON DELETE SET NULL,
  modality TEXT NOT NULL, region TEXT, performed_on TEXT NOT NULL,
  report_text TEXT, dicom_dir TEXT                    -- DICOM sin metadatos personales
);

-- Bitácora de llamadas a IA: sin contenido, solo lo necesario para auditar y controlar el gasto.
CREATE TABLE ai_call (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL DEFAULT (datetime('now')),
  task TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
  input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, request_id TEXT,
  person_id INTEGER REFERENCES person(id) ON DELETE SET NULL
);

-- Quién vio qué. El admin ve todo, pero queda registro.
CREATE TABLE access_log (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL DEFAULT (datetime('now')),
  actor_id INTEGER NOT NULL REFERENCES person(id), subject_id INTEGER NOT NULL REFERENCES person(id),
  action TEXT NOT NULL
);
