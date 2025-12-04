-- PostgreSQL schema helpers for double-entry enforcement
-- Ensure journals are balanced: sum(amount) = 0 per journal_id with tolerance
-- Enforce Account.ccy = Entry.ccy
-- Set default isolation level to SERIALIZABLE

-- Amount non-zero
ALTER TABLE IF EXISTS entries
    ADD CONSTRAINT IF NOT EXISTS ck_entries_amount_nonzero CHECK (amount <> 0);

-- Tolerance 1e-6 for balance
CREATE OR REPLACE FUNCTION check_journal_balanced() RETURNS TRIGGER AS $$
DECLARE
    v_total NUMERIC;
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO v_total
    FROM entries
    WHERE journal_id = COALESCE(NEW.journal_id, OLD.journal_id);

    IF abs(v_total) > 0.000001 THEN
        RAISE EXCEPTION 'Journal % is not balanced (total=%)', COALESCE(NEW.journal_id, OLD.journal_id), v_total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL; -- AFTER trigger
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_journal_balanced ON entries;
CREATE CONSTRAINT TRIGGER trg_check_journal_balanced
AFTER INSERT OR UPDATE OR DELETE ON entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION check_journal_balanced();

-- Enforce account currency on entries
CREATE OR REPLACE FUNCTION check_entry_ccy() RETURNS TRIGGER AS $$
DECLARE
    v_acc_ccy TEXT;
BEGIN
    SELECT ccy INTO v_acc_ccy FROM accounts WHERE id = NEW.account_id;
    IF v_acc_ccy IS NULL THEN
        RAISE EXCEPTION 'Account % not found', NEW.account_id;
    END IF;
    IF v_acc_ccy <> NEW.ccy THEN
        RAISE EXCEPTION 'Currency mismatch for account %: account=% entry=%', NEW.account_id, v_acc_ccy, NEW.ccy
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_check_entry_ccy ON entries;
CREATE TRIGGER trg_check_entry_ccy
BEFORE INSERT OR UPDATE ON entries
FOR EACH ROW EXECUTE FUNCTION check_entry_ccy();

-- Set default DB isolation (requires superuser)
DO $$ BEGIN
  EXECUTE 'ALTER DATABASE ' || current_database() || ' SET default_transaction_isolation = ''serializable''';
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'Skipping ALTER DATABASE, insufficient privileges';
END $$;
