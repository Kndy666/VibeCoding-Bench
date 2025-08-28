document.getElementById('fileInput').addEventListener('change', function(event) {
    loadFiles(event.target.files);
});

document.getElementById('prFilter').addEventListener('input', applyFilters);
document.getElementById('startDate').addEventListener('change', applyFilters);
document.getElementById('endDate').addEventListener('change', applyFilters);
document.getElementById('failToPassFilter').addEventListener('change', applyFilters);

window.onclick = function(event) {
    const modal = document.getElementById('prModal');
    if (event.target === modal) {
        modal.style.display = 'none';
    }

    const repoContainer = document.getElementById('repoFilterContainer');
    if (!repoContainer.contains(event.target)) {
        document.getElementById('repoDropdown').style.display = 'none';
    }
};
