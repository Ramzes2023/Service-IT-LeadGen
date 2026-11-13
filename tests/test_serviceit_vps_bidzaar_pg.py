import os
import pytest
from leadpilot.serviceit_bidzaar_worker import DDL,persist

@pytest.mark.skipif(os.getenv('SERVICEIT_PG_CI_EPHEMERAL')!='YES',reason='disposable CI PostgreSQL only')
def test_atomic_cursor_and_written_receipt_not_requeued():
    import psycopg
    with psycopg.connect(host='127.0.0.1',dbname='leadgen',user='leadgen',password='synthetic-ci-pg-password',autocommit=True) as conn:
        conn.execute(DDL)
        row={'link':'https://bidzaar.com/process/light/00000000-0000-0000-0000-000000000001','lastModified':'2026-11-11T01:00:00Z'}
        persist(conn,[row],row['lastModified'])
        conn.execute("UPDATE serviceit_bidzaar_inbox SET state='POST_UNCERTAIN'")
        newer={**row,'lastModified':'2026-11-11T02:00:00Z'}
        persist(conn,[newer],newer['lastModified'])
        assert conn.execute('SELECT state FROM serviceit_bidzaar_inbox').fetchone()[0]=='POST_UNCERTAIN'
        assert conn.execute('SELECT cursor FROM serviceit_bidzaar_cursor').fetchone()[0]==newer['lastModified']
        with pytest.raises(ValueError):persist(conn,[{**row,'link':'https://evil.test/'}],'2099-01-01T00:00:00Z')
        assert conn.execute('SELECT cursor FROM serviceit_bidzaar_cursor').fetchone()[0]==newer['lastModified']
        conn.execute('DROP TABLE serviceit_bidzaar_inbox; DROP TABLE serviceit_bidzaar_cursor')
