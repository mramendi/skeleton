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
                    // Initialize the chat app directly after login
                    setTimeout(async () => {
                        // Find the chat app instance and initialize it
                        const appContainer = document.getElementById('app-container');
                        if (appContainer && appContainer._x_dataStack && appContainer._x_dataStack[0]) {
                            const chatApp = appContainer._x_dataStack[0];
                            await chatApp.init();
                        }
                    }, 100);
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
        loadingStates: {}, // Per-thread loading states
        eventSource: null,
        documentScrollMode: false,
        mobileMenuOpen: false,
        offline: false,

        // Initialization
        async init() {
            console.log('[DEBUG] Initializing chatApp...');
            const token = localStorage.getItem('authToken');
            console.log('[DEBUG] Token check:', token ? 'present' : 'missing');
            
            if (!token) {
                console.log('[DEBUG] No auth token, showing login');
                document.getElementById('app-container').classList.add('hidden');
                document.getElementById('login-container').classList.remove('hidden');
                return;
            }
            
            // Ensure we're showing the app container
            document.getElementById('app-container').classList.remove('hidden');
            document.getElementById('login-container').classList.add('hidden');
            
            // Small delay to ensure DOM is ready
            await new Promise(resolve => setTimeout(resolve, 100));
            console.log('[DEBUG] Token confirmed, proceeding with data load...');

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

            // Only load data if we have a valid token
            try {
                console.log('[DEBUG] Loading models and threads...');
                await this.loadModels();
                await this.loadThreads();
                console.log('[DEBUG] Initialization complete');
            } catch (error) {
                console.error('[DEBUG] Failed to load initial data:', error);
                // If we get auth error, clear token and show login
                if (error.message && error.message.includes('Authentication required')) {
                    console.log('[DEBUG] Invalid token, clearing and showing login');
                    localStorage.removeItem('authToken');
                    document.getElementById('app-container').classList.add('hidden');
                    document.getElementById('login-container').classList.remove('hidden');
                }
            }
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
                console.log('[DEBUG] Loading models...');
                const response = await this.apiCall('/api/v1/models');
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                this.models = await response.json();
                console.log('[DEBUG] Loaded models:', this.models);
                if (this.models.length > 0) {
                    this.currentModel = this.models[0];
                    console.log('[DEBUG] Set current model to:', this.currentModel);
                } else {
                    console.warn('[WARNING] No models returned from API');
                    this.models = ['gpt-3.5-turbo']; // Fallback model
                    this.currentModel = this.models[0];
                }
            } catch (error) {
                console.error('Failed to load models:', error);
                // Don't show alert, just use fallback
                this.models = ['gpt-3.5-turbo']; // Fallback model
                this.currentModel = this.models[0];
                console.log('[DEBUG] Using fallback model due to error:', error.message);
            }
        },

        async loadThreads() {
            try {
                console.log('[DEBUG] Loading threads...');
                const response = await this.apiCall('/api/v1/threads');
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                this.threads = await response.json();
                console.log('[DEBUG] Loaded threads:', this.threads.length, 'threads');
            } catch (error) {
                console.error('Failed to load threads:', error);
                // Don't show alert, just start with empty threads
                this.threads = [];
                console.log('[DEBUG] Starting with empty threads due to error:', error.message);
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
            // Clear loading state for new thread
            this.setLoading(false);
        },

        async selectThread(threadId, title) {
            this.currentThreadId = threadId;
            this.currentThreadTitle = title;
            
            // Try to switch model to the thread's model
            const thread = this.threads.find(t => t.id === threadId);
            if (thread && thread.model) {
                // Check if the model exists in available models
                if (this.models.includes(thread.model)) {
                    this.currentModel = thread.model;
                    console.log(`[DEBUG] Switched model to thread model: ${thread.model}`);
                } else {
                    console.log(`[DEBUG] Thread model ${thread.model} not available, keeping current: ${this.currentModel}`);
                }
            }
            
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

                // Guard against scrollHeight being 0 (iOS Safari edge case)
                if (!scrollHeight) {
                    return true;
                }

                // Guard against NaN values
                if (!isFinite(scrollTop) || !isFinite(clientHeight) || !isFinite(scrollHeight)) {
                    return true;
                }

                const atBottom = scrollTop + clientHeight >= scrollHeight - 200; // 200px threshold

                if (Math.random() < 0.1) {
                    console.log('[DEBUG] Mobile scroll check:', { scrollTop, clientHeight, scrollHeight, atBottom });
                }
                return atBottom;
            } else {
                // Desktop check remains the same
                if (!this.$refs.messagesContainer) return true;
                const el = this.$refs.messagesContainer;
                const scrollTop = el.scrollTop;
                const clientHeight = el.clientHeight;
                const scrollHeight = el.scrollHeight;

                // Guard against NaN values
                if (!isFinite(scrollTop) || !isFinite(clientHeight) || !isFinite(scrollHeight)) {
                    return true;
                }

                return scrollTop + clientHeight >= scrollHeight - 100;
            }
        },

        // Message Handling
        async sendMessage() {
            if (!this.newMessage.trim() || this.isLoading()) return;

            this.setLoading(true);
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
                                    this.setLoading(false);
                                    // Refresh threads if new thread was created
                                    await this.loadThreads();
                                } else if (data.event === 'error') {
                                    console.error('Stream error:', data.data.message);
                                    assistantMessage.content = `Error: ${data.data.message}`;
                                    const index = this.messages.findIndex(m => m.id === assistantMessage.id);
                                    if (index !== -1) {
                                        this.messages[index] = { ...assistantMessage };
                                    }
                                    this.setLoading(false);
                                }
                            } catch (e) {
                                console.error('Error parsing SSE data:', e, line);
                            }
                        }
                    }
                }
            } catch (error) {
                console.error('Error in stream:', error);
                this.setLoading(false);
                this.offline = true;
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
        },

        // Loading state management methods
        isLoading() {
            return this.loadingStates[this.currentThreadId] || false;
        },

        setLoading(loading) {
            if (this.currentThreadId) {
                this.loadingStates[this.currentThreadId] = loading;
            }
        }
    }
}
