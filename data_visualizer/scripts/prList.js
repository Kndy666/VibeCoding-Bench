function generatePRList() {
    const prList = document.getElementById('prList');
    const prCount = document.getElementById('pr-count');
    const sortedPRs = getFilteredPrData()
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    prCount.textContent = `Total ${sortedPRs.length} items`;

    prList.innerHTML = sortedPRs.map((pr, index) => {
        const fileCount = pr.patch?.length || 0;
        const testFileCount = pr.test_patch?.length || 0;
        const changeCount = pr.patch?.reduce((sum, file) => sum + (file.changes || 0), 0) || 0;
        const additions = pr.patch?.reduce((sum, file) => sum + (file.additions || 0), 0) || 0;
        const deletions = pr.patch?.reduce((sum, file) => sum + (file.deletions || 0), 0) || 0;
        const failToPass = pr.FAIL_TO_PASS;

        const mainFiles = pr.patch?.slice(0, 3).map(file => {
            const filename = file.filename.split('/').pop();
            return filename;
        }) || [];

        const moreFiles = fileCount > 3 ? ` +${fileCount - 3} more` : '';

        const originalIndex = getPrData().findIndex(item => item === pr);

        return `
            <div class="pr-item" onclick="openPRDetail(${originalIndex})">
                <div class="pr-header">
                    <div>
                        <div class="pr-title">${pr.problem_statement?.substring(0, 120) || 'No description'}${pr.problem_statement?.length > 120 ? '...' : ''}</div>
                        <div class="pr-meta">
                            <span class="tag">${pr.repo || 'unknown'}</span>
                            <span class="tag">PR #${pr.number || 'N/A'}</span>
                            ${pr.created_at ? `<span style="margin-left: 10px;">${new Date(pr.created_at).toLocaleDateString()}</span>` : ''}
                        </div>
                    </div>
                </div>

                <div class="pr-stats">
                    <div class="pr-stat">
                        <span>📁</span>
                        <span>${fileCount} files</span>
                    </div>
                    <div class="pr-stat">
                        <span>🔄</span>
                        <span>${changeCount} line changes</span>
                    </div>
                    <div class="pr-stat">
                        <span style="color: #28a745;">+${additions}</span>
                    </div>
                    <div class="pr-stat">
                        <span style="color: #dc3545;">-${deletions}</span>
                    </div>
                    <div class="pr-stat">
                        <span>🧪</span>
                        <span>${testFileCount} test files</span>
                    </div>
                </div>

                <div class="pr-files">
                    <div class="pr-files-title">Main modified files:</div>
                    <div class="file-list">
                        ${mainFiles.map(file => `<span class="file-tag">${file}</span>`).join('')}
                        ${moreFiles ? `<span class="file-tag">...${moreFiles}</span>` : ''}
                    </div>
                </div>

                <div class="test-info">
                    <div class="test-info test-fail">
                        <strong>Fail to Pass tests:</strong> ${failToPass}
                    </div>
                </div>
            </div>
        `;
    }).join('');
}
