"""Dark persistence boundary for immutable DB-backed authority policy.

S1 intentionally has no production caller.  The shipped authority hook keeps
using ``Database.claim_authority_candidate`` for legacy/static-policy attempts,
which truthfully creates no sidecar pin.  A later integration slice will call
this store only after resolving an active DB release and activation.
"""

from __future__ import annotations

from runtime.infrastructure.database import Database
from runtime.models import (
    AuthorityCandidate,
    AuthorityCandidatePolicyPin,
    AuthorityPolicyActivation,
    AuthorityPolicyRelease,
)


class AuthorityPolicyStore:
    """Typed, test-callable facade over the transaction-owning DB methods."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def create_release(self, release: AuthorityPolicyRelease) -> AuthorityPolicyRelease:
        return self._db.create_authority_policy_release(release)

    def activate(self, activation: AuthorityPolicyActivation) -> AuthorityPolicyActivation:
        receipt = AuthorityPolicyActivation.model_validate(
            activation.model_dump(mode="python", round_trip=True, warnings=False)
        )
        return self._db.activate_authority_policy(receipt)

    def get_release(self, release_id: str) -> AuthorityPolicyRelease | None:
        return self._db.get_authority_policy_release(release_id)

    def get_activation(self, activation_id: str) -> AuthorityPolicyActivation | None:
        return self._db.get_authority_policy_activation(activation_id)

    def claim_candidate_with_pin(
        self,
        *,
        release_id: str,
        activation_id: str,
        activation_epoch: int,
        provider_id: str,
        executor_kind: str,
        **candidate_kwargs,
    ) -> tuple[AuthorityCandidate, AuthorityCandidatePolicyPin]:
        return self._db.claim_authority_candidate_with_policy_pin(
            release_id=release_id,
            activation_id=activation_id,
            activation_epoch=activation_epoch,
            provider_id=provider_id,
            executor_kind=executor_kind,
            **candidate_kwargs,
        )
