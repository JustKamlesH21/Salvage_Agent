"""The redactor must remove PII without changing the shape of the payload.

A redaction that shortens an id or drops a field produces a fixture that passes
tests the real payload would fail. That is worse than having no fixture: it is
a test that lies. These tests pin shape preservation as hard as they pin the
removal itself.
"""

from __future__ import annotations

import json

from scripts import redact_fixtures as rf


class TestStructurePreservation:
    def test_razorpay_ids_keep_prefix_and_length(self) -> None:
        for original in ("pay_29QQoUBi66xm2f", "sub_TWf80TVDUE1ABy", "plink_R4mKq2Xn1"):
            replaced = rf.redact_id(original)
            assert replaced != original
            assert len(replaced) == len(original)
            assert replaced.split("_")[0] == original.split("_")[0]

    def test_an_unknown_prefix_is_left_alone(self) -> None:
        assert rf.redact_id("not_an_rz_id") == "not_an_rz_id"

    def test_contacts_keep_their_digit_count_and_plus(self) -> None:
        original = "+919876543210"
        replaced = rf.redact_contact(original)
        assert replaced.startswith("+")
        assert len(replaced) == len(original)
        assert replaced != original

    def test_the_country_code_is_preserved(self) -> None:
        # +91 is not PII and it is load-bearing: anything routing or validating
        # on it must behave the same on the fixture as on the real payload.
        assert rf.redact_contact("+919876543210").startswith("+91")

    def test_the_result_is_a_well_formed_indian_mobile(self) -> None:
        # Right length is not enough — it has to be a number a parser accepts.
        replaced = rf.redact_contact("+919876543210")
        assert replaced[3] in "6789", replaced

    def test_a_bare_ten_digit_number_keeps_its_length(self) -> None:
        replaced = rf.redact_contact("9876543210")
        assert len(replaced) == 10 and not replaced.startswith("+")

    def test_emails_stay_email_shaped(self) -> None:
        replaced = rf.redact_email("real.person@gmail.com")
        assert "@" in replaced and replaced.endswith(".invalid")
        assert "real.person" not in replaced

    def test_names_keep_word_count_and_word_lengths(self) -> None:
        original = "Asha Ravikumar"
        replaced = rf.redact_name(original)
        # Derived, not hand-counted — a literal here is a place to miscount,
        # and this test exists precisely to catch length drift.
        assert [len(w) for w in replaced.split()] == [len(w) for w in original.split()]
        assert "Asha" not in replaced

    def test_a_redacted_name_contains_only_letters(self) -> None:
        # Same length is not enough: "Pac7qpmvs" is name-shaped only by
        # accident. Anything validating a name would treat it differently from
        # the real payload.
        replaced = rf.redact_name("Asha Ravikumar")
        assert replaced.replace(" ", "").isalpha(), replaced

    def test_the_document_keeps_every_key(self) -> None:
        payload = {
            "event": "payment.failed",
            "payload": {"payment": {"entity": {"id": "pay_x1", "email": "a@b.com", "amount": 1}}},
        }
        assert json.dumps(rf.walk(payload), sort_keys=True).count('"') == json.dumps(
            payload, sort_keys=True
        ).count('"')


class TestWhatIsKept:
    def test_error_fields_survive_verbatim(self) -> None:
        # SPEC §4.1 builds the taxonomy on exactly these five. Redacting any of
        # them would make day 3 impossible.
        entity = {
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Your card has been declined due to insufficient funds.",
            "error_source": "bank",
            "error_step": "payment_authorization",
            "error_reason": "insufficient_funds",
        }
        assert rf.walk(entity) == entity

    def test_amounts_and_timestamps_survive(self) -> None:
        entity = {
            "amount": 1500100,
            "currency": "INR",
            "created_at": 1788238151,
            "status": "failed",
        }
        assert rf.walk(entity) == entity

    def test_card_last4_and_network_survive(self) -> None:
        card = {"last4": "1111", "network": "Visa", "issuer": "HDFC", "type": "credit"}
        assert rf.walk(card) == card


class TestDeterminism:
    def test_the_same_input_always_redacts_identically(self) -> None:
        # Re-running the redactor must not churn git.
        payload = {"email": "x@y.com", "contact": "+919876543210", "id": "pay_29QQoUBi66xm2f"}
        assert rf.walk(payload) == rf.walk(payload)

    def test_different_inputs_redact_differently(self) -> None:
        assert rf.redact_email("a@x.com") != rf.redact_email("b@x.com")


class TestSignature:
    def test_the_recomputed_signature_verifies_over_the_redacted_bytes(self) -> None:
        import hashlib
        import hmac

        body = b'{"event":"payment.failed"}'
        expected = hmac.new(rf.FIXTURE_SECRET.encode(), body, hashlib.sha256).hexdigest()
        assert rf.sign(body) == expected

    def test_a_changed_byte_breaks_the_signature(self) -> None:
        assert rf.sign(b'{"a":1}') != rf.sign(b'{"a":2}')
