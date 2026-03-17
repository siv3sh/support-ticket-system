from services.report_services import *

def generate_insights():
    """This Function will provide the insight service for the tickets.
    Like the which tickets are increased, Open Status and other. """

    insights = []
    # imported from the report services file
    status = get_total_tickets_status()
    issues = get_issue_distribution()
    customers = get_tickets_by_customer()
    avg_resolution = get_average_resolution_time()

    total = status["total"]

    if total == 0:
        return ["No tickets available for analysis"]

    # Top issue insight
    if issues:
        top_issue = issues[0]
        percent = round((top_issue["count"] / total) * 100)

        insights.append(
            f"{top_issue['issue']} issues represent {percent}% of total tickets."
        )

    # Top customer insight
    if customers:
        top_customer = customers[0]
        percent = round((top_customer["tickets"] / total) * 100)

        insights.append(
            f"{top_customer['company']} generated {percent}% of support requests."
        )

    # Ticket status insight
    open_percent = round((status["open"] / total) * 100)

    insights.append(
        f"{open_percent}% of tickets are currently open and require attention."
    )

    # Resolution insight
    insights.append(
        f"The average ticket resolution time is {avg_resolution} hours."
    )

    return insights