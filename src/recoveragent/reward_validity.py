"""Shared validity gate. Invalid scores are not zero rewards or capability failures."""


def valid_reward(episode):
    return (
        episode.get("reward_valid", True) is True
        and type(episode.get("success")) is bool
        and episode.get("classification")
        not in {"infrastructure_error", "scoring_gap", "system_fault"}
        and not episode.get("fault")
    )


def recovery_failure(episode):
    return valid_reward(episode) and episode["success"] is False
