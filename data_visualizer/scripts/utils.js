function getLanguageFromFilename(filename) {
    if (!filename) return 'plaintext';
    const extension = filename.split('.').pop();
    switch (extension) {
        case 'py': return 'python';
        case 'js': return 'javascript';
        case 'cpp': case 'cc': case 'h': return 'cpp';
        default: return 'plaintext';
    }
}

function formatAndHighlightPatch(patchContent, language) {
    if (!patchContent) return 'No patch content available';

    const lines = escapeHtml(patchContent).split('\n');
    let html = '';

    for (const line of lines) {
        let lineClass = '';
        let codeLine = line;

        if (line.startsWith('+')) {
            lineClass = 'diff-add';
            codeLine = line.substring(1);
        } else if (line.startsWith('-')) {
            lineClass = 'diff-remove';
            codeLine = line.substring(1);
        } else if (line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')) {
            lineClass = 'diff-meta';
        } else {
            if (codeLine.startsWith(' ')) {
               codeLine = codeLine.substring(1);
            }
        }

        const highlighted = language === 'plaintext'
            ? codeLine
            : hljs.highlight(codeLine, { language: language, ignoreIllegals: true }).value;

        html += `<span class="diff-line ${lineClass}">${highlighted || '&nbsp;'}</span>`;
    }
    return html;
}

function generateDiffView(patches) {
    if (!patches || patches.length === 0) {
        return '<p style="color: #666; font-style: italic;">No changes</p>';
    }

    return patches.map(file => {
        const language = getLanguageFromFilename(file.filename);
        return `
            <div class="diff-container">
                <div class="diff-header">
                    ${file.filename}
                    <span style="float: right; font-size: 0.9em;">
                        <span style="color: #28a745;">+${file.additions || 0}</span>
                        <span style="color: #dc3545;"> -${file.deletions || 0}</span>
                        <span style="color: #666;"> (${file.changes || 0} changes)</span>
                    </span>
                </div>
                <div class="diff-content">
                    <pre><code>${formatAndHighlightPatch(file.patch || 'No patch content available', language)}</code></pre>
                </div>
            </div>
        `;
    }).join('');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
