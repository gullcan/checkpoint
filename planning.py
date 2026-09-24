def calculate_work_minutes(
    estimated_minutes,
    available_minutes,
    session_limit=15,
):
    if available_minutes <= 0:
        return 0

    return min(
        estimated_minutes,
        available_minutes,
        session_limit,
    )