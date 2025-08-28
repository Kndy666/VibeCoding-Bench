/**
 * @file dataAnalyzer.js
 * @description Analyzes PR data and generates statistics and charts.
 * This file has been updated to use only 'fail to pass' PRs for chart generation,
 * as requested by the user.
 */

/**
 * Main analysis function.
 * Called after data is loaded to generate all stats, charts, and lists.
 */
function analyzeData() {
    // Check if there is any data to analyze.
    if (getPrData().length === 0) return;

    // Update the main statistics, which still use all data for context.
    updateStats();

    // Create the charts using a filtered subset of the data.
    createCharts();

    // Populate the repository filter dropdown with all available repositories.
    populateRepoFilter();

    // Generate the PR list, which respects the current filters.
    generatePRList();
}

/**
 * Updates the main statistics cards.
 * These stats use both the full dataset and the 'fail to pass' subset for context.
 */
function updateStats() {
    const prData = getPrData();
    const totalPRs = prData.length;

    // Filter the PR data to only include those that failed and then passed.
    const failToPassPRsData = prData.filter(pr => pr.FAIL_TO_PASS);
    const failToPassPRsCount = failToPassPRsData.length;

    const totalRepos = new Set(prData.map(pr => pr.repo)).size;

    const failToPassReposCount = new Set(failToPassPRsData.map(pr => pr.repo)).size;

    document.getElementById('totalPRs').textContent = totalPRs;
    document.getElementById('failToPassPRs').textContent = failToPassPRsCount;
    document.getElementById('totalRepos').textContent = totalRepos;
    document.getElementById('failToPassRepos').textContent = failToPassReposCount;
}

/**
 * Orchestrates the creation of all charts.
 * This function now filters the data to use only 'fail to pass' samples
 * before calling the individual chart functions.
 */
function createCharts() {
    // Filter the data to get only the PRs that failed and then passed their tests.
    const failToPassPrs = getPrData().filter(pr => pr.FAIL_TO_PASS);

    // Pass this filtered data to each chart creation function.
    createFileDistChart(failToPassPrs);
    createChangesChart(failToPassPrs);
    createRepoChart(failToPassPrs);
    createTimelineChart(failToPassPrs);
}

/**
 * Creates the File Modification Distribution chart.
 * @param {Array<Object>} prData - The PR data to analyze.
 */
function createFileDistChart(prData) {
    const filesCounts = prData.map(pr => pr.patch?.length || 0);
    const distribution = { '1-2': 0, '3-5': 0, '6-10': 0, '10+': 0 };

    filesCounts.forEach(count => {
        const range = count <= 2 ? '1-2' : count <= 5 ? '3-5' : count <= 10 ? '6-10' : '10+';
        distribution[range] = (distribution[range] || 0) + 1;
    });

    const canvas = document.getElementById('fileDistChart');
    if (!canvas) return;
    const existingChart = Chart.getChart(canvas);
    if (existingChart) existingChart.destroy();

    const ctx = canvas.getContext('2d');
    new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: Object.keys(distribution),
            datasets: [{
                data: Object.values(distribution),
                backgroundColor: [
                    'rgba(102, 126, 234, 0.8)',
                    'rgba(118, 75, 162, 0.8)',
                    'rgba(255, 99, 132, 0.8)',
                    'rgba(255, 206, 84, 0.8)'
                ],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    position: 'bottom'
                }
            }
        }
    });
}

/**
 * Creates the Code Change Distribution chart.
 * @param {Array<Object>} prData - The PR data to analyze.
 */
function createChangesChart(prData) {
    const changes = prData.map(pr => {
        return pr.patch?.reduce((sum, file) => sum + (file.changes || 0), 0) || 0;
    });

    const distribution = { '1-10': 0, '11-50': 0, '51-100': 0, '100+': 0 };
    changes.forEach(change => {
        const range = change <= 10 ? '1-10' : change <= 50 ? '11-50' : change <= 100 ? '51-100' : '100+';
        distribution[range] = (distribution[range] || 0) + 1;
    });

    const canvas = document.getElementById('changesChart');
    if (!canvas) return;
    const existingChart = Chart.getChart(canvas);
    if (existingChart) existingChart.destroy();

    const ctx = canvas.getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: Object.keys(distribution),
            datasets: [{
                label: 'PR Count',
                data: Object.values(distribution),
                backgroundColor: 'rgba(102, 126, 234, 0.8)',
                borderColor: 'rgba(102, 126, 234, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        stepSize: 1
                    }
                }
            }
        }
    });
}

/**
 * Creates the Repository Distribution chart.
 * @param {Array<Object>} prData - The PR data to analyze.
 */
function createRepoChart(prData) {
    const repos = {};
    prData.forEach(pr => {
        const repo = pr.repo || 'unknown';
        repos[repo] = (repos[repo] || 0) + 1;
    });

    const sortedRepos = Object.entries(repos)
        .sort((a, b) => b[1] - a[1]);

    const canvas = document.getElementById('repoChart');
    if (!canvas) return;
    const existingChart = Chart.getChart(canvas);
    if (existingChart) existingChart.destroy();

    const ctx = canvas.getContext('2d');
    new Chart(ctx, {
        type: 'bar',
        data: {
            labels: sortedRepos.map(([repo]) => repo.split('/').pop() || repo),
            datasets: [{
                label: 'PR Count',
                data: sortedRepos.map(([, count]) => count),
                backgroundColor: 'rgba(118, 75, 162, 0.8)',
                borderColor: 'rgba(118, 75, 162, 1)',
                borderWidth: 1
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    ticks: {
                        stepSize: 1
                    }
                }
            }
        }
    });
}

/**
 * Creates the Monthly PR Trends chart.
 * @param {Array<Object>} prData - The PR data to analyze.
 */
function createTimelineChart(prData) {
    const timeline = {};
    prData.forEach(pr => {
        if (pr.created_at) {
            const date = new Date(pr.created_at);
            const monthKey = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
            timeline[monthKey] = (timeline[monthKey] || 0) + 1;
        }
    });

    const sortedTimeline = Object.entries(timeline).sort();

    const canvas = document.getElementById('timelineChart');
    if (!canvas) return;
    const existingChart = Chart.getChart(canvas);
    if (existingChart) existingChart.destroy();

    const ctx = canvas.getContext('2d');
    new Chart(ctx, {
        type: 'line',
        data: {
            labels: sortedTimeline.map(([month]) => month),
            datasets: [{
                label: 'PR Count',
                data: sortedTimeline.map(([, count]) => count),
                borderColor: 'rgba(102, 126, 234, 1)',
                backgroundColor: 'rgba(102, 126, 234, 0.1)',
                fill: true,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        stepSize: 1
                    }
                }
            }
        }
    });
}