// Skeleton Chat - Alpine.js Components
console.log('[DEBUG] app.js loaded successfully');
// Show alert on mobile to confirm script loaded
if (window.matchMedia('(max-width: 768px)').matches) {
    console.log('[DEBUG] Mobile detected - scripts loaded');
}

// Login App
function loginApp() {
    return {
        username: '',
        password: '',
        loading: false,
        error: '',

        async handleLogin() {
            this.loading = true;
            this.error = '';

            try {
                const response = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username: this.username,
                        password: this.password
                    })
                });

                const data = await response.json();

                if (response.ok) {
                    localStorage.setItem('authToken', data.access_token);
                    document.getElementById('app-container').classList.remove('hidden');
                    document.getElementById('login-container').classList.add('hidden');
                } else {
                    this.error = data.detail || 'Login failed';
                }
            } catch (error) {
                this.error = 'Network error';
            } finally {
                this.loading = false;
            }
        }
    }
}

// Chat App
function chatApp() {
    return {
        // State
        messages: [],
        threads: [],
        models: [],
        currentThreadId: null,
        currentThreadTitle: 'New Chat',
        currentModel: 'gpt-3.5-turbo',
        currentSystemPrompt: 'default',
        newMessage: '',
        searchQuery: '',
        loading: false,
        eventSource: null,
        documentScrollMode: false,
        mobileMenuOpen: false,

        // Initialization
        async init() {
            console.log('[DEBUG] Initializing chatApp...');
            const token = localStorage.getItem('authToken');
            if (!token) {
                console.log('[DEBUG] No auth token, showing login');
                document.getElementById('app-container').classList.add('hidden');
                document.getElementById('login-container').classList.remove('hidden');
                return;
            }

            // Load scroll mode preference (desktop only)
            const isMobile = window.matchMedia('(max-width: 768px)').matches;
            console.log('[DEBUG] Mobile detected:', isMobile);
            if (!isMobile) {
                const scrollMode = localStorage.getItem('scrollMode');
                if (scrollMode === 'document') {
                    this.documentScrollMode = true;
                    document.body.classList.add('document-scroll');
                }
            }

            // Configure marked.js to use highlight.js for code blocks
            marked.setOptions({
                highlight: function(code, lang) {
                    if (lang && hljs.getLanguage(lang)) {
                        try {
                            return hljs.highlight(code, { language: lang }).value;
                        } catch (err) {
                            console.error('Highlight.js error:', err);
                        }
                    }
                    return hljs.highlightAuto(code).value;
                },
                breaks: true,  // Convert \n to <br>
                gfm: true      // GitHub Flavored Markdown
            });

            console.log('[DEBUG] Loading models and threads...');
            await this.loadModels();
            await this.loadThreads();
            console.log('[DEBUG] Initialization complete');
        },

        // API Methods
        async apiCall(url, options = {}) {
            const token = localStorage.getItem('authToken');
            const response = await fetch(url, {
                ...options,
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json',
                    ...options.headers
                }
            });

            if (response.status === 401) {
                this.logout();
                throw new Error('Authentication required');
            }

            return response;
        },

        async loadModels() {
            try {
                const response = await this.apiCall('/api/v1/models');
                this.models = await response.json();
                console.log('[DEBUG] Loaded models:', this.models);
                if (this.models.length > 0) {
                    this.currentModel = this.models[0];
                    console.log('[DEBUG] Set current model to:', this.currentModel);
                } else {
                    console.error('[ERROR] No models returned from API');
                    alert('Error: No models available. Check backend logs.');
                }
            } catch (error) {
                console.error('Failed to load models:', error);
                alert('Failed to load models: ' + error.message);
            }
        },

        async loadThreads() {
            try {
                const response = await this.apiCall('/api/v1/threads');
                this.threads = await response.json();
                console.log('[DEBUG] Loaded threads:', this.threads.length, 'threads');
            } catch (error) {
                console.error('Failed to load threads:', error);
                alert('Failed to load threads: ' + error.message);
            }
        },

        async searchThreads() {
            if (!this.searchQuery) {
                await this.loadThreads();
                return;
            }

            try {
                const response = await this.apiCall(`/api/v1/search?q=${encodeURIComponent(this.searchQuery)}`);
                const results = await response.json();
                this.threads = results;
            } catch (error) {
                console.error('Failed to search threads:', error);
            }
        },

        startNewThread() {
            // Clear current thread to start a new conversation
            this.currentThreadId = null;
            this.currentThreadTitle = 'New Chat';
            this.messages = [];
        },

        async selectThread(threadId, title) {
            this.currentThreadId = threadId;
            this.currentThreadTitle = title;
            await this.loadThreadMessages(threadId);
        },

        async loadThreadMessages(threadId) {
            try {
                const response = await this.apiCall(`/api/v1/threads/${threadId}/messages`);
                const messages = await response.json();
                this.messages = messages.map((msg, index) => ({
                    ...msg,
                    id: `${threadId}-${index}`
                }));
                this.$nextTick(() => {
                    this.scrollToBottom();
                });
            } catch (error) {
                console.error('Failed to load messages:', error);
            }
        },

        scrollToBottom() {
            const isMobile = window.matchMedia('(max-width: 768px)').matches;

            if (isMobile || this.documentScrollMode) {
                requestAnimationFrame(() => {
                    // Use the standard window.scrollTo method which is the most reliable.
                    const scrollHeight = document.documentElement.scrollHeight;
                    window.scrollTo({ top: scrollHeight, behavior: 'auto' });
                    console.log(`[DEBUG] Scrolling WINDOW to: ${scrollHeight}`);
                });
            } else {
                // Desktop fixed scroll mode
                if (this.$refs.messagesContainer) {
                    this.$refs.messagesContainer.scrollTop = this.$refs.messagesContainer.scrollHeight;
                }
            }
        },

        // ✅ Replace your existing isScrolledToBottom function with this one

        isScrolledToBottom() {
            const isMobile = window.matchMedia('(max-width: 768px)').matches;

            if (isMobile || this.documentScrollMode) {
                // Use cross-browser compatible properties to get scroll and viewport height.
                const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
                const clientHeight = document.documentElement.clientHeight;
                const scrollHeight = document.documentElement.scrollHeight;

                const atBottom = scrollTop + clientHeight >= scrollHeight - 200; // 200px threshold

                if (Math.random() < 0.1) {
                    console.log('[DEBUG] Mobile scroll check:', { scrollTop, clientHeight, scrollHeight, atBottom });
                }
                return atBottom;
            } else {
                // Desktop check remains the same
                if (!this.$refs.messagesContainer) return true;
                const el = this.$refs.messagesContainer;
                return el.scrollTop + el.clientHeight >= el.scrollHeight - 100;
            }
        },

        // Message Handling
        async sendMessage() {
            if (!this.newMessage.trim() || this.loading) return;

            this.loading = true;
            console.log('[DEBUG] Sending message...');

            // Add user message
            const userMessage = {
                id: Date.now().toString(),
                role: 'user',
                content: this.newMessage,
                timestamp: new Date().toISOString()
            };
            this.messages.push(userMessage);
            console.log('[DEBUG] User message added. Total messages:', this.messages.length);

            // Clear input
            const messageContent = this.newMessage;
            this.newMessage = '';

            this.$nextTick(() => {
                console.log('[DEBUG] Scrolling to bottom...');
                this.scrollToBottom();
            });

            // Start SSE stream
            await this.startStream(messageContent);
        },

        async startStream(content) {
            try {
                const token = localStorage.getItem('authToken');
                const formData = new FormData();
                formData.append('content', content);
                if (this.currentThreadId) formData.append('thread_id', this.currentThreadId);
                formData.append('model', this.currentModel);
                formData.append('system_prompt', this.currentSystemPrompt);

                const response = await fetch('/api/v1/message', {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    },
                    body: formData
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                // Create assistant message
                let assistantMessage = {
                    id: Date.now().toString() + '-assistant',
                    role: 'assistant',
                    content: '',
                    timestamp: new Date().toISOString()
                };
                this.messages.push(assistantMessage);
                console.log('[DEBUG] Assistant message added. Total messages:', this.messages.length);

                // Read the stream
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // Keep incomplete line in buffer

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.slice(6));

                                if (data.event === 'thread_id') {
                                    // Update current thread ID if new thread was created
                                    this.currentThreadId = data.data.thread_id;
                                    console.log('[DEBUG] Thread ID set to:', this.currentThreadId);
                                } else if (data.event === 'message_tokens') {
                                    assistantMessage.content += data.data.content;
                                    // Update the message in place - use splice for better reactivity
                                    const index = this.messages.findIndex(m => m.id === assistantMessage.id);
                                    if (index !== -1) {
                                        this.messages.splice(index, 1, { ...assistantMessage });
                                    }
                                    // Log every 50 chars to avoid spam
                                    if (assistantMessage.content.length % 50 < data.data.content.length) {
                                        console.log('[DEBUG] Message content length:', assistantMessage.content.length);
                                    }
                                    // Auto-scroll only if user is already at bottom
                                    const wasAtBottom = this.isScrolledToBottom();
                                    this.$nextTick(() => {
                                        if (wasAtBottom) {
                                            this.scrollToBottom(); // This now handles the animation frame itself
                                        } else if (Math.random() < 0.05) {
                                            console.log('[DEBUG] NOT auto-scrolling - user scrolled up');
                                        }
                                    });
                                } else if (data.event === 'stream_end') {
                                    console.log('[DEBUG] Stream ended. Final message length:', assistantMessage.content.length);
                                    this.loading = false;
                                    // Refresh threads if new thread was created
                                    await this.loadThreads();
                                } else if (data.event === 'error') {
                                    console.error('Stream error:', data.data.message);
                                    assistantMessage.content = `Error: ${data.data.message}`;
                                    const index = this.messages.findIndex(m => m.id === assistantMessage.id);
                                    if (index !== -1) {
                                        this.messages[index] = { ...assistantMessage };
                                    }
                                    this.loading = false;
                                }
                            } catch (e) {
                                console.error('Error parsing SSE data:', e, line);
                            }
                        }
                    }
                }
            } catch (error) {
                console.error('Error in stream:', error);
                this.loading = false;
            }
        },

        // Utility Methods
        toggleLayout() {
            this.documentScrollMode = !this.documentScrollMode;
            if (this.documentScrollMode) {
                document.body.classList.add('document-scroll');
                // Save preference
                localStorage.setItem('scrollMode', 'document');
            } else {
                document.body.classList.remove('document-scroll');
                localStorage.setItem('scrollMode', 'fixed');
            }
        },

        logout() {
            localStorage.removeItem('authToken');
            document.getElementById('app-container').classList.add('hidden');
            document.getElementById('login-container').classList.remove('hidden');
        },

        renderMarkdown(content) {
            if (!content) return '';
            try {
                // Parse markdown to HTML
                const html = marked.parse(content);
                // Sanitize HTML to prevent XSS attacks
                return DOMPurify.sanitize(html);
            } catch (err) {
                console.error('Markdown parsing error:', err);
                // Fallback to escaped text if markdown parsing fails
                return content.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            }
        },

        formatDate(dateString) {
            return new Date(dateString).toLocaleDateString();
        },

        formatTime(dateString) {
            return new Date(dateString).toLocaleTimeString();
        }
    }
}
