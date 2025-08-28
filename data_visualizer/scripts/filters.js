function applyFilters() {
    const prQuery = document.getElementById('prFilter').value.toLowerCase();
    const selectedRepoNodes = document.querySelectorAll('#repoDropdown input[type="checkbox"]:checked');
    const selectedRepos = Array.from(selectedRepoNodes).map(cb => cb.value);
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;
    const failToPassChecked = document.getElementById('failToPassFilter').checked;

    const filtered = getPrData().filter(pr => {
        const matchesTitle = pr.problem_statement?.toLowerCase().includes(prQuery);
        const matchesRepo = selectedRepos.length === 0 || selectedRepos.includes(pr.repo?.toLowerCase());
        const matchesFailToPass = !failToPassChecked || pr.FAIL_TO_PASS;

        let matchesDate = true;
        if (startDate) {
            const prDate = new Date(pr.created_at);
            const start = new Date(startDate);
            start.setHours(0, 0, 0, 0);
            if (prDate < start) {
                matchesDate = false;
            }
        }
        if (endDate) {
            const prDate = new Date(pr.created_at);
            const end = new Date(endDate);
            end.setHours(23, 59, 59, 999);
            if (prDate > end) {
                matchesDate = false;
            }
        }

        return matchesTitle && matchesRepo && matchesDate && matchesFailToPass;
    });
    setFilteredPrData(filtered);
    updateSelectedReposDisplay();
    generatePRList();
}

function populateRepoFilter() {
    const repoDropdown = document.getElementById('repoDropdown');
    const repos = new Set();
    getPrData().forEach(pr => {
        if (pr.repo) {
            repos.add(pr.repo);
        }
    });

    repoDropdown.innerHTML = '';
    const sortedRepos = Array.from(repos).sort();
    sortedRepos.forEach(repo => {
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = repo.toLowerCase();
        checkbox.onchange = applyFilters;
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(' ' + repo));
        repoDropdown.appendChild(label);
    });
}

function toggleRepoDropdown() {
    const dropdown = document.getElementById('repoDropdown');
    dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
}

function updateSelectedReposDisplay() {
    const selectedContainer = document.getElementById('selectedRepos');
    const placeholder = document.getElementById('repoPlaceholder');
    const selectedRepoNodes = document.querySelectorAll('#repoDropdown input[type="checkbox"]:checked');
    const selectedCount = selectedRepoNodes.length;

    selectedContainer.innerHTML = '';
    if (selectedCount === 0) {
        placeholder.style.display = 'inline';
    } else if (selectedCount === 1) {
        placeholder.style.display = 'none';
        const repoName = selectedRepoNodes[0].parentElement.textContent.trim();
        const tag = document.createElement('span');
        tag.className = 'selected-option-tag';
        tag.textContent = repoName;
        selectedContainer.appendChild(tag);
    } else {
        placeholder.style.display = 'none';
        const tag = document.createElement('span');
        tag.className = 'selected-option-tag';
        tag.textContent = `Selected ${selectedCount} repositories`;
        selectedContainer.appendChild(tag);
    }
}
