import sqlite3
from importlib.resources import files


def test_schema_loads_and_enforces_constraints():
    sql = files("house.db").joinpath("schema.sql").read_text(encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.executescript(sql)
    db.execute(
        "INSERT INTO person(display_name,birth_date,sex_at_birth,is_admin) VALUES('A','1980-01-01','M',1)"
    )
    db.execute(
        "INSERT INTO document(person_id,doc_type,title,file_path,file_sha256) VALUES(1,'laboratorio','x','/f','abc')"
    )
    try:
        db.execute(
            "INSERT INTO document(person_id,doc_type,title,file_path,file_sha256) VALUES(1,'laboratorio','x','/f','abc')"
        )
        raise AssertionError("debe rechazar duplicados")
    except sqlite3.IntegrityError:
        pass
    try:
        db.execute("INSERT INTO person(display_name,birth_date,sex_at_birth) VALUES('B','1980-01-01','X')")
        raise AssertionError("debe rechazar sexo inválido")
    except sqlite3.IntegrityError:
        pass
    db.execute("DELETE FROM person WHERE id=1")
    assert db.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 0  # cascada: borrar perfil borra todo
