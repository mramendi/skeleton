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
        systemPrompts: {},
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
                console.log('[DEBUG] Loading models, system prompts, and threads...');
                await this.loadModels();
                await this.loadSystemPrompts();
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

        async loadSystemPrompts() {
            try {
                console.log('[DEBUG] Loading system prompts...');
                const response = await this.apiCall('/api/v1/system_prompts');
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
                }
                this.systemPrompts = await response.json();
                console.log('[DEBUG] Loaded system prompts:', this.systemPrompts);

                // Set default to first available prompt if current selection is not found
                if (!this.systemPrompts[this.currentSystemPrompt]) {
                    const availableKeys = Object.keys(this.systemPrompts);
                    if (availableKeys.length > 0) {
                        this.currentSystemPrompt = availableKeys[0];
                        console.log('[DEBUG] Set system prompt to first available:', this.currentSystemPrompt);
                    }
                }
            } catch (error) {
                console.error('Failed to load system prompts:', error);
                // Fallback to basic prompts
                this.systemPrompts = {
                    'default': 'General purpose assistant',
                    'zero': 'No system prompt'
                };
                console.log('[DEBUG] Using fallback system prompts due to error:', error.message);
            }
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
            // Use a temporary ID for new threads until we get the real ID from server
            this.currentThreadId = `temp-${Date.now()}`;
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
            if (thread) {
                // Handle model selection
                if (thread.model) {
                    // Check if the model exists in available models
                    if (this.models.includes(thread.model)) {
                        this.currentModel = thread.model;
                        console.log(`[DEBUG] Switched model to thread model: ${thread.model}`);
                    } else if (thread.model === "MODELS NOT AVAILABLE") {
                        console.log(`[DEBUG] Thread model shows MODELS NOT AVAILABLE - API key issue detected`);
                        this.currentModel = "MODELS NOT AVAILABLE";
                    } else {
                        console.log(`[DEBUG] Thread model ${thread.model} not available, keeping current: ${this.currentModel}`);
                    }
                }

                // Handle system prompt selection
                if (thread.system_prompt) {
                    // Check if the system prompt exists in available prompts
                    if (this.systemPrompts.hasOwnProperty(thread.system_prompt)) {
                        this.currentSystemPrompt = thread.system_prompt;
                        console.log(`[DEBUG] Switched system prompt to thread prompt: ${thread.system_prompt}`);
                    } else {
                        console.log(`[DEBUG] Thread system prompt ${thread.system_prompt} not available, using default`);
                        this.currentSystemPrompt = "default";
                    }
                }
            }

            await this.loadThreadMessages(threadId);
        },

        async loadThreadMessages(threadId) {
            try {
                const response = await this.apiCall(`/api/v1/threads/${threadId}/messages`);
                const messages = await response.json();

                // Group consecutive thinking and assistant messages, and tool messages by call_id
                const groupedMessages = [];
                let currentGroup = null;
                let toolGroups = {};  // Track tool messages by call_id

                for (let i = 0; i < messages.length; i++) {
                    const msg = messages[i];

                    // Handle tool messages with call_id - group by call_id
                    if (msg.role === 'tool' && msg.type === 'tool_update' && msg.call_id) {
                        // Close any active assistant group
                        if (currentGroup) {
                            currentGroup.content = currentGroup.segments
                                .filter(s => s.type === 'message')
                                .map(s => s.content)
                                .join('');
                            groupedMessages.push(currentGroup);
                            currentGroup = null;
                        }

                        // Find or create tool group for this call_id
                        if (!toolGroups[msg.call_id]) {
                            toolGroups[msg.call_id] = {
                                id: `tool-${msg.call_id}`,
                                role: 'tool',
                                content: '',
                                timestamp: msg.timestamp,
                                call_id: msg.call_id
                            };
                            groupedMessages.push(toolGroups[msg.call_id]);
                        }
                        // Append content to existing tool group
                        toolGroups[msg.call_id].content += msg.content + '\n';
                    }
                    // If this is a thinking or assistant message and we have a current group
                    else if ((msg.role === 'thinking' || msg.role === 'assistant') && currentGroup) {
                        // Add as a segment to current group
                        currentGroup.segments.push({
                            type: msg.role === 'thinking' ? 'thinking' : 'message',
                            content: msg.content || '',
                            timestamp: msg.timestamp,
                            isCollapsed: msg.role === 'thinking'  // Thinking segments start collapsed
                        });
                    }
                    // If this is a thinking or assistant message and no current group, start a new group
                    else if (msg.role === 'thinking' || msg.role === 'assistant') {
                        currentGroup = {
                            id: `${threadId}-${i}`,
                            role: 'assistant',  // Combined group is always 'assistant'
                            content: '',  // Will be built from segments
                            timestamp: msg.timestamp,
                            segments: [{
                                type: msg.role === 'thinking' ? 'thinking' : 'message',
                                content: msg.content || '',
                                timestamp: msg.timestamp,
                                isCollapsed: msg.role === 'thinking'  // Thinking segments start collapsed
                            }]
                        };
                    }
                    // If this is any other message type, close current group and add as separate message
                    else {
                        if (currentGroup) {
                            // Build content from message segments
                            currentGroup.content = currentGroup.segments
                                .filter(s => s.type === 'message')
                                .map(s => s.content)
                                .join('');
                            groupedMessages.push(currentGroup);
                            currentGroup = null;
                        }

                        // Add non-assistant, non-tool message as separate bubble
                        groupedMessages.push({
                            ...msg,
                            id: `${threadId}-${i}`
                        });
                    }
                }

                // Don't forget to add the last group if it exists
                if (currentGroup) {
                    currentGroup.content = currentGroup.segments
                        .filter(s => s.type === 'message')
                        .map(s => s.content)
                        .join('');
                    groupedMessages.push(currentGroup);
                }

                this.messages = groupedMessages;
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
                // Only send thread_id if it's a real thread (not a temporary one)
                if (this.currentThreadId && !this.currentThreadId.startsWith('temp-')) {
                    formData.append('thread_id', this.currentThreadId);
                }
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

                // --- FIX: Create an ID first ---
                let assistantMessageId = Date.now().toString() + '-assistant';

                // Create and push the assistant message
                let plainAssistantMessage = {
                    id: assistantMessageId,
                    role: 'assistant',
                    content: '',
                    timestamp: new Date().toISOString(),
                    segments: []  // Initialize segments array
                };
                this.messages.push(plainAssistantMessage);
                console.log('[DEBUG] Assistant message added. Total messages:', this.messages.length);

                // Flag to track if a tool/function call has occurred
                let toolCallOccurred = false;

                // Track tool/function bubbles by call_id
                let toolBubbles = {};  // call_id -> bubble_id mapping

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

                                // --- FIX: Find the reactive message from this.messages ---
                                let assistantMessage = this.messages.find(m => m.id === assistantMessageId);

                                if (data.event === 'thread_id') {
                                    // Update current thread ID if new thread was created
                                    // Transfer loading state from temp ID to real thread ID
                                    const oldThreadId = this.currentThreadId;
                                    const wasLoading = this.loadingStates[oldThreadId];
                                    if (wasLoading && oldThreadId.startsWith('temp-')) {
                                        delete this.loadingStates[oldThreadId];
                                    }
                                    this.currentThreadId = data.data.thread_id;
                                    if (wasLoading) {
                                        this.loadingStates[this.currentThreadId] = true;
                                    }
                                    console.log('[DEBUG] Thread ID set to:', this.currentThreadId);
                                } else if (data.event === 'message_tokens') {
                                    // Add message as a separate segment to maintain order
                                    if (assistantMessage) {
                                        // Initialize segments array if not present
                                        if (!assistantMessage.segments) {
                                            assistantMessage.segments = [];
                                        }

                                        // Find if there's an existing message segment to append to
                                        let lastSegment = assistantMessage.segments[assistantMessage.segments.length - 1];
                                        if (lastSegment && lastSegment.type === 'message') {
                                            // Append to existing message segment
                                            lastSegment.content += data.data.content;
                                        } else {
                                            // Create new message segment
                                            assistantMessage.segments.push({
                                                type: 'message',
                                                content: data.data.content,
                                                timestamp: data.data.timestamp,
                                                isCollapsed: false  // Regular messages start expanded
                                            });
                                        }

                                        // Also update the legacy content field for compatibility
                                        assistantMessage.content = assistantMessage.segments
                                            .filter(s => s.type === 'message')
                                            .map(s => s.content)
                                            .join('');

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
                                    }
                                } else if (data.event === 'thinking_tokens') {
                                    // Add thinking tokens to the last thinking segment
                                    if (assistantMessage) {
                                        // Initialize segments array if not present
                                        if (!assistantMessage.segments) {
                                            assistantMessage.segments = [];
                                        }

                                        // Find if there's an existing thinking segment to append to
                                        let lastSegment = assistantMessage.segments[assistantMessage.segments.length - 1];
                                        if (lastSegment && lastSegment.type === 'thinking') {
                                            // Append to existing thinking segment
                                            lastSegment.content += data.data.content;
                                        } else {
                                            // Create new thinking segment (and ensure it starts collapsed)
                                            assistantMessage.segments.push({
                                                type: 'thinking',
                                                content: data.data.content,
                                                timestamp: data.data.timestamp,
                                                isCollapsed: true
                                            });
                                        }

                                        // Auto-scroll
                                        const wasAtBottom = this.isScrolledToBottom();
                                        this.$nextTick(() => {
                                            if (wasAtBottom) {
                                                this.scrollToBottom();
                                            }
                                        });
                                    }
                                } else if (data.event === 'tool_update') {
                                    const callId = data.data.call_id;

                                    // Create new tool bubble if this is the first tool_update for this call_id
                                    if (!toolBubbles[callId]) {
                                        const bubbleId = `tool-${callId}`;
                                        toolBubbles[callId] = bubbleId;
                                        this.messages.push({
                                            id: bubbleId,
                                            role: 'tool',
                                            content: '',
                                            timestamp: new Date().toISOString(),
                                            call_id: callId
                                        });
                                        console.log('[DEBUG] Created new tool bubble:', bubbleId, 'for call_id:', callId);
                                    }

                                    // Find the tool message for this call_id and append content
                                    let toolMessage = this.messages.find(m => m.id === toolBubbles[callId]);
                                    if (toolMessage) {
                                        toolMessage.content += data.data.content + '\n';
                                    }

                                    // Handle creating new assistant bubble after tool call (only once, after tool bubble is created)
                                    if (!toolCallOccurred) {
                                        toolCallOccurred = true;

                                        // Check if current assistant message is empty
                                        const isEmpty = !assistantMessage.segments ||
                                                        assistantMessage.segments.length === 0 ||
                                                        assistantMessage.segments.every(s => !s.content);

                                        if (isEmpty) {
                                            // Remove the empty assistant message
                                            const index = this.messages.findIndex(m => m.id === assistantMessageId);
                                            if (index !== -1) {
                                                this.messages.splice(index, 1);
                                                console.log('[DEBUG] Removed empty assistant message before tool call');
                                            }
                                        }

                                        // Create a new assistant message that will receive responses after the tool call
                                        assistantMessageId = Date.now().toString() + '-assistant-after-tool';
                                        plainAssistantMessage = {
                                            id: assistantMessageId,
                                            role: 'assistant',
                                            content: '',
                                            timestamp: new Date().toISOString(),
                                            segments: []
                                        };
                                        this.messages.push(plainAssistantMessage);
                                        console.log('[DEBUG] Created new assistant message after tool call');
                                    }

                                    // Auto-scroll
                                    const wasAtBottom = this.isScrolledToBottom();
                                    this.$nextTick(() => {
                                        if (wasAtBottom) {
                                            this.scrollToBottom();
                                        }
                                    });
                                } else if (data.event === 'stream_end') {
                                    console.log('[DEBUG] Stream ended. Final message length:', assistantMessage ? assistantMessage.content.length : 'N/A');
                                    this.setLoading(false);
                                    // Refresh threads if new thread was created
                                    await this.loadThreads();
                                } else if (data.event === 'error') {
                                    console.error('Stream error:', data.data.message);
                                    // Create a separate error message bubble
                                    const errorMessage = {
                                        id: Date.now().toString() + '-error',
                                        role: 'error',
                                        content: `Error: ${data.data.message}`,
                                        timestamp: new Date().toISOString()
                                    };
                                    this.messages.push(errorMessage);
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
            this.loadingStates[this.currentThreadId] = loading;
        }
    }
}
