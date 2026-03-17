let trendChart
let issueChart
let customerChart

async function loadDashboard(){

const res = await fetch("/api/dashboard")
const data = await res.json()

updateStats(data.stats, data.avg_time)
drawTrend(data.trend)
drawIssues(data.issues)
drawCustomers(data.customers)
drawAgents(data.agents)

}

function updateStats(stats, avg){

document.getElementById("totalTickets").innerText = stats.total
document.getElementById("openTickets").innerText = stats.open
document.getElementById("pendingTickets").innerText = stats.pending
document.getElementById("closedTickets").innerText = stats.closed
document.getElementById("avgTime").innerText = avg + "h"

}

function drawTrend(trend){

const labels = trend.map(t => t.date)
const values = trend.map(t => t.tickets)

if(trendChart) trendChart.destroy()

trendChart = new Chart(document.getElementById("trendChart"), {

type:'line',

data:{
labels:labels,
datasets:[{
label:'Tickets',
data:values
}]
}

})

}

function drawIssues(issues){

const labels = issues.map(i => i.issue)
const values = issues.map(i => i.count)

if(issueChart) issueChart.destroy()

issueChart = new Chart(document.getElementById("issueChart"), {

type:'pie',

data:{
labels:labels,
datasets:[{data:values}]
}

})

}

function drawCustomers(customers){

const labels = customers.map(c => c.company)
const values = customers.map(c => c.tickets)

if(customerChart) customerChart.destroy()

customerChart = new Chart(document.getElementById("customerChart"), {

type:'bar',

data:{
labels:labels,
datasets:[{data:values}]
}

})

}

function drawAgents(agents){

const table = document.querySelector("#agentTable tbody")

table.innerHTML = ""

agents.forEach(a => {

const row = `<tr>
<td>${a._id || "Unassigned"}</td>
<td>${a.tickets}</td>
<td>${a.closed}</td>
</tr>`

table.innerHTML += row

})

}

loadDashboard()

setInterval(loadDashboard,5000)