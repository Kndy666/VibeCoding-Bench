let prData = [];
let filteredPrData = [];

function loadFiles(files) {
    if (files.length === 0) return;

    document.getElementById('loading').classList.add('show');
    document.getElementById('analysisResults').style.display = 'none';

    Promise.all(Array.from(files).map(file => {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const data = JSON.parse(e.target.result);
                    resolve(Array.isArray(data) ? data : [data]);
                } catch (error) {
                    reject(error);
                }
            };
            reader.readAsText(file);
        });
    })).then(results => {
        prData = results.flat();
        filteredPrData = [...prData];
        analyzeData();
        document.getElementById('loading').classList.remove('show');
        document.getElementById('analysisResults').style.display = 'block';
    }).catch(error => {
        console.error('File reading error:', error);
        const errorMessage = document.createElement('div');
        errorMessage.textContent = 'File format error, please ensure uploading valid JSON files';
        errorMessage.style.cssText = `
            position: fixed;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            background-color: #f8d7da;
            color: #721c24;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            z-index: 1001;
        `;
        document.body.appendChild(errorMessage);
        setTimeout(() => document.body.removeChild(errorMessage), 3000);
        document.getElementById('loading').classList.remove('show');
    });
}

function getPrData() {
    return prData;
}

function getFilteredPrData() {
    return filteredPrData;
}

function setFilteredPrData(data) {
    filteredPrData = data;
}
