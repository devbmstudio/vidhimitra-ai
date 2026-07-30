class VidhiMitraChat {
    constructor() {
        this.apiBase = localStorage.getItem('api_base') || '';
        this.sessionId = localStorage.getItem('session_id') || this.generateId();
        localStorage.setItem('session_id', this.sessionId);
        this.typingTimer = null;
        this.isProcessing = false;
        this.init();
    }

    generateId() {
        return 'vm_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8);
    }

    init() {
        this.cacheDom();
        this.bindEvents();
        this.showGreeting();
    }

    cacheDom() {
        this.messagesEl = document.getElementById('chat-messages');
        this.inputEl = document.getElementById('chat-input');
        this.sendBtn = document.getElementById('send-btn');
        this.uploadBtn = document.getElementById('upload-btn');
        this.fileInput = document.getElementById('file-input');
        this.chipsContainer = document.getElementById('quick-chips');
        this.chatContainer = document.getElementById('chat-container');
        this.typingEl = document.getElementById('typing-indicator');
    }

    bindEvents() {
        this.sendBtn.addEventListener('click', () => this.sendMessage());
        this.inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        this.uploadBtn.addEventListener('click', () => this.fileInput.click());
        this.fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                this.uploadFile(e.target.files[0]);
            }
        });
        this.chipsContainer.addEventListener('click', (e) => {
            const chip = e.target.closest('.chip');
            if (chip) {
                this.handleChipClick(chip.dataset.action);
            }
        });
    }

    showGreeting() {
        const greeting = "Hey! I know finding government docs and scholarships is a real headache. Don't worry, I'll do the digging for you. Just tell me what you need!";
        this.addBotMessage(greeting);
    }

    addBotMessage(text) {
        this.addMessage(text, false);
    }

    addUserMessage(text) {
        this.addMessage(text, true);
    }

    addMessage(text, isUser) {
        const div = document.createElement('div');
        div.className = `message ${isUser ? 'user' : 'bot'}`;

        if (!isUser) {
            const avatar = document.createElement('div');
            avatar.className = 'bot-avatar';
            avatar.textContent = 'VM';
            div.appendChild(avatar);
        }

        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.innerHTML = this.renderMarkdown(text);
        div.appendChild(bubble);

        this.messagesEl.appendChild(div);
        this.scrollToBottom();
    }

    renderMarkdown(text) {
        let html = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/\*(.+?)\*/g, '<em>$1</em>')
            .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
            .replace(/\n/g, '<br>');
        return html;
    }

    showTyping(text) {
        const el = document.getElementById('typing-text');
        if (el) el.textContent = text || '';
        this.typingEl.style.display = 'flex';
        this.scrollToBottom();
    }

    hideTyping() {
        this.typingEl.style.display = 'none';
    }

    scrollToBottom() {
        setTimeout(() => {
            this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
        }, 50);
    }

    async sendMessage(text) {
        if (this.isProcessing) return;

        const message = text || this.inputEl.value.trim();
        if (!message) return;

        this.inputEl.value = '';
        this.isProcessing = true;
        this.sendBtn.disabled = true;

        this.addUserMessage(message);
        this.showTyping("VidhiMitra is searching government sources...");

        try {
            const resp = await fetch(`${this.apiBase}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: message,
                    session_id: this.sessionId,
                }),
            });

            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }

            const data = await resp.json();
            this.hideTyping();
            this.addBotMessage(data.reply);
            this.updateChips(data.quick_chips || []);
        } catch (err) {
            this.hideTyping();
            this.addBotMessage("I'm having trouble reaching the server. Make sure the backend is running at " + this.apiBase);
            console.error('Chat error:', err);
        } finally {
            this.isProcessing = false;
            this.sendBtn.disabled = false;
            this.inputEl.focus();
        }
    }

    async uploadFile(file) {
        if (file.type !== 'application/pdf') {
            this.addBotMessage('Please upload a PDF file only.');
            return;
        }

        this.addUserMessage(`(Uploaded: ${file.name})`);
        this.showTyping("VidhiMitra is analyzing your document...");

        const formData = new FormData();
        formData.append('file', file);

        try {
            const resp = await fetch(`${this.apiBase}/upload`, {
                method: 'POST',
                body: formData,
            });

            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'Upload failed');
            }

            const data = await resp.json();
            this.hideTyping();

            this.showDocumentResult(data);
        } catch (err) {
            this.hideTyping();
            this.addBotMessage(`Error: ${err.message}`);
        }

        this.fileInput.value = '';
    }

    showDocumentResult(data) {
        if (data.error) {
            this.addBotMessage(data.error);
            return;
        }

        let msg = '';

        if (data.explanation) {
            msg += `**📄 ${data.details?.doc_type || 'Document'}**\n\n`;
            msg += `*${data.explanation}*\n\n`;

            if (data.details?.scheme_name) {
                msg += `**Scheme:** ${data.details.scheme_name}\n`;
            }
            if (data.details?.amount) {
                msg += `**Amount:** ₹${data.details.amount}\n`;
            }
            if (data.details?.application_deadline) {
                msg += `**Deadline:** ${data.details.application_deadline}\n`;
            }
            if (data.details?.portal) {
                msg += `**Portal:** ${data.details.portal}\n`;
            }
            if (data.details?.provider) {
                msg += `**Provider:** ${data.details.provider}\n`;
            }
            if (data.details?.category) {
                msg += `**Category:** ${data.details.category}\n`;
            }
            if (data.details?.education_level) {
                msg += `**Education Level:** ${data.details.education_level}\n`;
            }
            if (data.details?.state) {
                msg += `**State:** ${data.details.state}\n`;
            }

            if (data.details?.action_items && data.details.action_items.length > 0) {
                msg += '\n**What to do next:**\n';
                data.details.action_items.forEach((item, i) => {
                    msg += `${i+1}. ${item}\n`;
                });
            }

            this.addBotMessage(msg);
            this.renderMarkdown(msg);

            if (data.can_help) {
                this.showInsightPrompt(data);
            }
        } else {
            this.addBotMessage(data.message || 'Could not analyze this document.');
        }
    }

    showInsightPrompt(data) {
        const div = document.createElement('div');
        div.className = 'message bot';
        div.innerHTML = `
            <div class="bot-avatar">VM</div>
            <div class="bubble insight-prompt">
                <p style="margin-bottom:8px;">🤝 <strong>Help others?</strong></p>
                <p style="font-size:13px;color:#666;margin-bottom:10px;">
                    Share anonymous document details so others can identify this document too.
                    No personal data, no document content — just the scheme name, amount, and type.
                </p>
                <div style="display:flex;gap:8px;">
                    <button class="chip insight-yes" style="background:#FF9933;color:white;border:none;">Yes, help others</button>
                    <button class="chip insight-no" style="background:#f0f0f0;color:#666;">No thanks</button>
                </div>
            </div>
        `;
        this.messagesEl.appendChild(div);
        this.scrollToBottom();

        div.querySelector('.insight-yes').addEventListener('click', () => {
            this.submitInsight(data.details);
            div.querySelector('.insight-prompt').innerHTML =
                '<p style="color:#2e7d32;">✅ Anonymous insight saved. Thank you for helping others!</p>';
        });
        div.querySelector('.insight-no').addEventListener('click', () => {
            div.querySelector('.insight-prompt').innerHTML =
                '<p style="color:#666;">No problem! Your document was not stored.</p>';
        });
    }

    async submitInsight(details) {
        try {
            await fetch(`${this.apiBase}/insights`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(details),
            });
        } catch (err) {
            console.error('Insight submit error:', err);
        }
    }

    handleChipClick(action) {
        this.sendMessage(action);
    }

    updateChips(chips) {
        this.chipsContainer.innerHTML = '';
        if (!chips || chips.length === 0) {
            chips = [
                { label: 'Latest Acts', action: 'Show me the latest acts' },
                { label: 'Find Scholarships', action: 'Find scholarships for me' },
                { label: 'Closing Soon', action: 'Show scholarships closing soon' },
                { label: 'Search by Ministry', action: 'Search by ministry' },
            ];
        }
        chips.forEach(chip => {
            const btn = document.createElement('button');
            btn.className = 'chip';
            btn.dataset.action = chip.action || chip.label;
            btn.textContent = chip.label;
            this.chipsContainer.appendChild(btn);
        });
    }

    setApiBase(url) {
        this.apiBase = url;
        localStorage.setItem('api_base', url);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.chat = new VidhiMitraChat();
});
