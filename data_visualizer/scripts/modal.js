let currentFontSize = 14;

function openPRDetail(index) {
    const pr = getPrData()[index];
    const modal = document.getElementById('prModal');
    const modalTitle = document.getElementById('modalTitle');
    const modalBody = document.getElementById('modalBody');

    modalTitle.textContent = `PR #${pr.number || 'N/A'} - ${pr.repo || 'Unknown'}`;

    const changeCount = pr.patch?.reduce((sum, file) => sum + (file.changes || 0), 0) || 0;
    const additions = pr.patch?.reduce((sum, file) => sum + (file.additions || 0), 0) || 0;
    const deletions = pr.patch?.reduce((sum, file) => sum + (file.deletions || 0), 0) || 0;

    const renderedMarkdown = pr.problem_statement ? marked.parse(pr.problem_statement) : 'No description';

    modalBody.innerHTML = `
        <div class="section-title">Problem Description</div>
        <div class="markdown-content">
            ${renderedMarkdown}
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 25px;">
            <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                <div style="font-size: 1.5em; font-weight: bold; color: #667eea;">${pr.patch?.length || 0}</div>
                <div style="color: #666;">Modified files</div>
            </div>
            <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                <div style="font-size: 1.5em; font-weight: bold; color: #667eea;">${changeCount}</div>
                <div style="color: #666;">Total changes</div>
            </div>
            <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                <div style="font-size: 1.5em; font-weight: bold; color: #28a745;">+${additions}</div>
                <div style="color: #666;">Additions</div>
            </div>
            <div style="text-align: center; padding: 15px; background: #f8f9fa; border-radius: 8px;">
                <div style="font-size: 1.5em; font-weight: bold; color: #dc3545;">-${deletions}</div>
                <div style="color: #666;">Deletions</div>
            </div>
        </div>

        <div class="section-title">Code Changes</div>
        ${generateDiffView(pr.patch || [])}

        ${pr.test_patch && pr.test_patch.length > 0 ? `
            <div class="section-title">Test Changes</div>
            ${generateDiffView(pr.test_patch)}

            ${pr.FAIL_TO_PASS ? `
                <div class="section-title">Test Results</div>
                <div class="test-info test-fail">
                    <strong>Fail to Pass tests:</strong> ${pr.FAIL_TO_PASS}
                </div>
            ` : ''}
        ` : ''}

        <div class="section-title">Metadata</div>
        <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; font-family: monospace;">
            <div><strong>Repository:</strong> ${pr.repo || 'N/A'}</div>
            <div><strong>PR Number:</strong> #${pr.number || 'N/A'}</div>
            <div><strong>Created At:</strong> ${pr.created_at ? new Date(pr.created_at).toLocaleString() : 'N/A'}</div>
            <div><strong>Base Commit:</strong> ${pr.base_commit?.substring(0, 8) || 'N/A'}</div>
            <div><strong>Version:</strong> ${pr.version || 'N/A'}</div>
            ${pr.instance_id ? `<div><strong>Instance ID:</strong> ${pr.instance_id}</div>` : ''}
        </div>
    `;

    modal.style.display = 'block';
    currentFontSize = 14;
    updateFontSize();
}

function closePRModal() {
    document.getElementById('prModal').style.display = 'none';
}

function updateFontSize() {
    const diffContents = document.querySelectorAll('#modalBody .diff-content');
    diffContents.forEach(el => {
        el.style.fontSize = `${currentFontSize}px`;
    });
}

function increaseFontSize() {
    if (currentFontSize < 24) {
        currentFontSize += 1;
        updateFontSize();
    }
}

function decreaseFontSize() {
    if (currentFontSize > 10) {
        currentFontSize -= 1;
        updateFontSize();
    }
}
