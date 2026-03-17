from datetime import datetime, timedelta

from database import tickets, customers

def get_total_tickets_status():
    """
    This function will return the total Tickets which are Open,Closed,Pending
    """
    total= tickets.count_documents({})
    opened= tickets.count_documents({"status":"Open"})
    pending= tickets.count_documents({"status":"Pending"})
    closed= tickets.count_documents({"status":"Closed"})

    return {
        "total": total,
        "open": opened,
        'pending': pending,
        'closed' : closed
    }

def get_ticket_trend(days=30):
    """
    This function will return the total no.of tickets created per day
    """

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)

    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": start_date}
            }
        },
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$created_at"},
                    "month": {"$month": "$created_at"},
                    "day": {"$dayOfMonth": "$created_at"}
                },
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"_id": 1}}
    ]

    result = list(tickets.aggregate(pipeline))

    trend_map = {}

    for r in result:
        date = f"{r['_id']['year']}-{r['_id']['month']:02}-{r['_id']['day']:02}"
        trend_map[date] = r["count"]

    trend = []

    current = start_date

    while current <= end_date:

        date_str = current.strftime("%Y-%m-%d")

        trend.append({
            "date": date_str,
            "tickets": trend_map.get(date_str, 0)
        })

        current += timedelta(days=1)

    return trend

def get_tickets_by_customer():
    """
    This function will return the no.of tickets by the customer
    """

    pipeline = [
        {
            "$group": {
                "_id": "$company_id",
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"count": -1}}
    ]

    result = list(tickets.aggregate(pipeline))

    data = []

    for r in result:
        company = customers.find_one({"_id": r["_id"]})

        name = "Unknown"
        if company:
            name = company.get("company_name", r["_id"])

        data.append({
            "company": name,
            "tickets": r["count"]
        })

    return data

def get_issue_distribution(days=None):
    """
    This function will Returns number of tickets by issue type.
    If days is provided, it filters tickets in that specific time period.
    """

    match_stage = {}

    if days:
        start_date = datetime.utcnow() - timedelta(days=days)
        match_stage = {
            "created_at": {"$gte": start_date}
        }

    pipeline = []

    if match_stage:
        pipeline.append({"$match": match_stage})

    pipeline.extend([
        {
            "$group": {
                "_id": "$issue",
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"count": -1}}
    ])

    result = list(tickets.aggregate(pipeline))

    return [
        {"issue": r["_id"] or "Unknown", "count": r["count"]}
        for r in result
    ]

def get_average_resolution_time():
    """
    This function will Calculates average time to close tickets.
    """

    closed_tickets = tickets.find({"status": "Closed"})

    total_time = 0
    count = 0

    for t in closed_tickets:
        created = t.get("created_at")
        updated = t.get("updated_at")

        if created and updated:
            diff = (updated - created).total_seconds()
            total_time += diff
            count += 1

    if count == 0:
        return 0

    avg_hours = total_time / count / 3600

    return round(avg_hours, 2)

def get_agent_performance():
    """This function will Check for agent performance"""

    pipeline = [

        {
            "$group": {
                "_id": "$agent_id",
                "tickets": {"$sum": 1},
                "closed": {
                    "$sum": {
                        "$cond": [{"$eq": ["$status", "Closed"]}, 1, 0]
                    }
                }
            }
        }

    ]

    return list(tickets.aggregate(pipeline))

def get_top_issue():
    """This Function will generate the top issue TICKET"""

    pipeline = [
        {
            "$match": {"issue": {"$ne": None}}
        },
        {
            "$group": {
                "_id": "$issue",
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": 1}
    ]

    result = list(tickets.aggregate(pipeline))

    if not result:
        return {"issue": "Unknown", "count": 0}

    issue = result[0]["_id"] or "Unknown"

    return {
        "issue": issue,
        "count": result[0]["count"]
    }


def get_top_customer():

    pipeline = [
        {
            "$match": {"company_id": {"$ne": None}}
        },
        {
            "$group": {
                "_id": "$company_id",
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"count": -1}},
        {"$limit": 1}
    ]

    result = list(tickets.aggregate(pipeline))

    if not result:
        return {"company": "Unknown", "count": 0}

    company_id = result[0]["_id"]

    company = customers.find_one({"_id": company_id})

    name = company.get("company_name") if company else company_id

    return {
        "company": name,
        "count": result[0]["count"]
    }


def get_ticket_volume():

    """
    Returns total ticket count.
    """

    return tickets.count_documents({})



def get_average_resolution():

    """
    Returns average resolution time in hours.
    """

    return get_average_resolution_time() # above function is called here

def get_kpi_summary(days):
    """
    Returns important KPIs for executive summary for the report
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    status = get_total_tickets_status_period(days)

    avg_resolution = get_average_resolution_time()

    customers = get_tickets_by_customer()
    issues = get_issue_distribution()

    top_customer = "N/A"
    top_issue = "N/A"

    if customers:
        top_customer = customers[0]["company"]

    if issues:
        top_issue = issues[0]["issue"]

    return {
        "total_tickets": status["total"],
        "avg_resolution_time": avg_resolution,
        "top_customer": top_customer,
        "top_issue": top_issue
    }


def get_sla_metrics():
    """
    Returns SLA monitoring metrics for the report
    """

    now = datetime.utcnow()

    last_24 = now - timedelta(hours=24)
    last_48 = now - timedelta(hours=48)

    tickets_24 = tickets.count_documents({
        "created_at": {"$lte": last_24},
        "status": {"$ne": "Closed"}
    })

    tickets_48 = tickets.count_documents({
        "created_at": {"$lte": last_48},
        "status": {"$ne": "Closed"}
    })

    unassigned = tickets.count_documents({
        "$or": [
            {"agent_id": None},
            {"agent_id": ""}
        ],
        "status": {"$ne": "Closed"}
    })

    return {
        "older_24": tickets_24,
        "older_48": tickets_48,
        "unassigned": unassigned
    }

def get_total_tickets_status_period(days):
    """This function will generate the total tickets on time period"""

    start_date = datetime.utcnow() - timedelta(days=days)

    total = tickets.count_documents({
        "created_at": {"$gte": start_date}
    })

    opened = tickets.count_documents({
        "status": "Open",
        "created_at": {"$gte": start_date}
    })

    pending = tickets.count_documents({
        "status": "Pending",
        "created_at": {"$gte": start_date}
    })

    closed = tickets.count_documents({
        "status": "Closed",
        "created_at": {"$gte": start_date}
    })

    return {
        "total": total,
        "open": opened,
        "pending": pending,
        "closed": closed
    }

def get_ticket_trend_monthly(months):
    """
    Returns ticket trend grouped by month for 6 months / yearly reports
    """

    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=months * 30)

    pipeline = [
        {
            "$match": {
                "created_at": {"$gte": start_date}
            }
        },
        {
            "$group": {
                "_id": {
                    "year": {"$year": "$created_at"},
                    "month": {"$month": "$created_at"}
                },
                "count": {"$sum": 1}
            }
        },
        {"$sort": {"_id.year": 1, "_id.month": 1}}
    ]

    result = list(tickets.aggregate(pipeline))

    trend = []

    for r in result:
        month_str = f"{r['_id']['year']}-{r['_id']['month']:02}"
        trend.append({
            "month": month_str,
            "tickets": r["count"]
        })

    return trend