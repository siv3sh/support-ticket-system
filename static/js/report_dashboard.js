document.addEventListener("DOMContentLoaded", function(){

console.log("Dashboard Loaded");

function readReportData() {
    var el = document.getElementById("report-data");
    if (!el) return { trend: [], issues: [], customers: [] };
    try {
        return JSON.parse(el.textContent || "{}") || { trend: [], issues: [], customers: [] };
    } catch (e) {
        return { trend: [], issues: [], customers: [] };
    }
}

var reportData = readReportData();
var trendData = reportData.trend || [];
var issueData = reportData.issues || [];
var customerData = reportData.customers || [];

console.log("Trend:", trendData);
console.log("Issue:", issueData);
console.log("Customer:", customerData);


/* GLOBAL CHART VARIABLES */

let trendChart = null;


/* TREND CHART RENDER FUNCTION */

function renderTrendChart(data){

    const labels = data.map(t => t.date);
    const values = data.map(t => t.tickets ?? t.count);

    if(trendChart){
        trendChart.destroy();
    }

    trendChart = new Chart(document.getElementById("ticketTrend"),{

        type:"line",

        data:{
            labels:labels,
            datasets:[{
                label:"Tickets",
                data:values,
                borderColor:"#0d9488",
                backgroundColor:"rgba(13,148,136,0.2)",
                fill:true,
                tension:0.3
            }]
        },

        options:{
            responsive:true,
            maintainAspectRatio:false
        }

    });

}


/* INITIAL TREND CHART LOAD */

if(Array.isArray(trendData) && trendData.length){
    renderTrendChart(trendData);
}


/* TREND FILTER CHANGE (7 / 14 / 30 days) */

const trendFilter = document.getElementById("trendFilter");

if(trendFilter){

trendFilter.addEventListener("change", function(){

    const days = this.value;

    fetch(`/reports/trend?days=${days}`)
    .then(res => res.json())
    .then(data => {

        console.log("Trend API Data:", data);

        renderTrendChart(data);

    })
    .catch(err => console.error("Trend fetch error:", err));

});

}


/* ISSUE DISTRIBUTION PIE CHART */

if(Array.isArray(issueData) && issueData.length){

const labels = issueData.map(i => {

    let name = i.issue ?? i._id ?? "Unknown";

    const maxLength = 18;

    if(name.length > maxLength){
        name = name.substring(0, maxLength) + "...";
    }

    return name;

});

const values = issueData.map(i => i.count);

new Chart(document.getElementById("issueChart"),{

type:"pie",

data:{
labels:labels,
datasets:[{
data:values,
backgroundColor:[
"#3b82f6",
"#10b981",
"#f59e0b",
"#ef4444",
"#8b5cf6",
"#14b8a6"
]
}]
},

options:{
responsive:true,
maintainAspectRatio:false,

plugins:{
legend:{
position:'right',
labels:{
boxWidth:12,
padding:8,
font:{
size:13
}
}
}
}

}

});

}


/* TICKETS BY CUSTOMER BAR CHART */

if(Array.isArray(customerData) && customerData.length){

const labels = customerData.map(c => c.company ?? c._id);
const values = customerData.map(c => c.tickets ?? c.count);

new Chart(document.getElementById("customerChart"),{

type:"bar",

data:{
labels:labels,
datasets:[{
label:"Tickets",
data:values,
backgroundColor:"#3b82f6"
}]
},

options:{
responsive:true,
maintainAspectRatio:false
}

});

}

});

function updateSLAColors(){

const sla24 = parseInt(document.querySelector("#sla24 p").innerText);
const sla48 = parseInt(document.querySelector("#sla48 p").innerText);
const unassigned = parseInt(document.querySelector("#slaUnassigned p").innerText);

const card24 = document.getElementById("sla24");
const card48 = document.getElementById("sla48");
const cardUn = document.getElementById("slaUnassigned");

/* 24 hour SLA */

if(sla24 === 0){
card24.classList.add("sla-good");
}else{
card24.classList.add("sla-warning");
}

/* 48 hour SLA */

if(sla48 === 0){
card48.classList.add("sla-good");
}else{
card48.classList.add("sla-danger");
}

/* Unassigned */

if(unassigned === 0){
cardUn.classList.add("sla-good");
}else{
cardUn.classList.add("sla-warning");
}

}

updateSLAColors();