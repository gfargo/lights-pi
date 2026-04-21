# Lights Pi - Product Roadmap

## Current State (v1.0)

**What We Have:**
- Headless Raspberry Pi lighting controller
- QLC+ web interface on port 9999
- Comprehensive CLI toolkit (`lightsctl.sh`)
- HTTPS support with mkcert
- Dual WiFi configuration
- Backup/restore functionality
- Health monitoring and diagnostics
- Landing page with nginx reverse proxy
- Complete documentation

**What Works Well:**
- Reliable DMX output via ENTTEC USB Pro
- Easy provisioning and setup
- Network accessibility (mDNS)
- Security hardening
- Modular script architecture

---

## Immediate Priorities (v1.1 - Next 2-4 weeks)

### 1. Auto-Load Default Workspace ✅ IMPLEMENTED
**Status:** Complete  
**Impact:** High - Major UX improvement

**What:**
- `set-default-workspace` command
- Workspace auto-loads on boot
- All users see same control board
- No manual loading required

**Implementation:**
- Modify systemd service to use `--open` flag
- Store workspace at `~/.qlcplus/default.qxw`
- Update on every boot

---

### 2. Marketing Website (v1.0)
**Status:** Planned  
**Impact:** Critical - Drives adoption  
**Timeline:** 2-3 weeks

**What:**
- Professional marketing site
- Interactive demo
- Complete documentation
- Showcase gallery
- Community hub

**Pages:**
- Homepage with hero demo
- Quick Start guide
- Hardware shopping list
- Use case examples
- Comparison with commercial solutions
- Community showcase
- Blog/updates

**Tech Stack:**
- Astro or Next.js
- Tailwind CSS
- Vercel hosting
- Plausible analytics

**Deliverables:**
- Complete site design
- All content written
- SEO optimized
- Mobile responsive
- Launch announcement

---

## Near-Term Features (v1.2-1.3 - Next 1-3 months)

### 3. AI Scene Generation (v1.2)
**Status:** Designed  
**Impact:** High - Unique differentiator  
**Timeline:** 3-4 weeks

**What:**
- Natural language scene creation
- Two style profiles: Complete and Modular
- Fixture-aware generation
- Multiple variations
- Scene library system

**Commands:**
```bash
./lightsctl.sh generate-scene "warm sunset ambiance" --style complete
./lightsctl.sh generate-scene "party mode" --style modular --variations 3
./lightsctl.sh scene-library list --tag warm
```

**Implementation:**
- Claude/GPT API integration
- Fixture inventory parser
- Scene XML generator
- Validation system
- Library management

**Dependencies:**
- AI API key (Anthropic/OpenAI)
- JSON/XML parsing tools
- Scene library structure

---

### 4. Scene Library & Marketplace (v1.3)
**Status:** Planned  
**Impact:** Medium-High - Community building  
**Timeline:** 2-3 weeks after AI generation

**What:**
- Curated scene library
- Community contributions
- Scene ratings and reviews
- Search and filtering
- One-click installation

**Structure:**
```
scenes/
├── complete/          # Ready-to-use scenes
├── modular/
│   ├── colors/
│   ├── intensities/
│   └── positions/
└── library.json       # Metadata
```

**Features:**
- Browse by tag, style, difficulty
- Preview before install
- Compatibility checking
- Version control
- Community voting

---

### 5. Enhanced Health Dashboard (v1.3)
**Status:** Planned  
**Impact:** Medium - Professional polish  
**Timeline:** 1-2 weeks

**What:**
- Web-based health dashboard
- Real-time metrics
- Historical data
- Alert system
- Performance graphs

**Metrics:**
- Service uptime
- DMX output status
- CPU/memory usage
- Network connectivity
- Temperature monitoring
- Error logs

**Access:**
- `https://lights.local/health/`
- Auto-refresh every 5s
- Mobile-friendly
- Export reports

---

## Mid-Term Features (v2.0 - Next 3-6 months)

### 6. Mobile PWA (Progressive Web App)

**Status:** Deferred (current QLC+ mobile interface is good)  
**Impact:** Medium - Enhanced mobile experience  
**Timeline:** TBD based on user feedback

**What:**
- Native-like mobile app experience
- Offline capability
- Push notifications
- Home screen installation
- Touch-optimized controls

**Features:**
- Custom virtual console layouts
- Gesture controls (swipe, pinch, etc.)
- Quick scene access
- Fixture grouping
- Preset management

**Why Deferred:**
- QLC+ web interface already works well on mobile
- Virtual console pages display correctly
- Focus on other priorities first
- Can revisit based on community demand

---

### 7. Advanced QLC+ Integration

**Workspace Versioning:**
- Git-like version control for workspaces
- Diff viewer for changes
- Rollback to previous versions
- Branch/merge workflows
- Conflict resolution
- Collaborative editing

**Fixture Profile Marketplace:**
- Community-contributed fixture definitions
- Automated testing and validation
- Rating and review system
- Installation tracking
- Update notifications
- Compatibility checking

**Scene Templates:**
- Pre-built templates for common scenarios
- YouTube studio setup (three-point lighting)
- Photography lighting (portrait, product, dramatic)
- Live streaming (dynamic, camera-friendly)
- Event production (concert, wedding, corporate)
- Template customization wizard

**Macro System:**
- Record and playback lighting sequences
- Conditional logic (if/then)
- Time-based triggers
- External event triggers
- Macro library sharing

---

### 8. Multi-Device Management

**Fleet Management:**
- Control multiple Pis from one CLI
- Synchronized scene deployment
- Health monitoring across fleet
- Centralized backup
- Group operations
- Role-based access

**Use Cases:**
- Multi-room studios (control all rooms from one interface)
- Large venues (multiple lighting zones)
- Rental operations (manage customer installations)
- Educational institutions (lab equipment management)

**Commands:**
```bash
./lightsctl.sh --fleet studio1,studio2,studio3 deploy-workspace show.qxw
./lightsctl.sh --fleet all health
./lightsctl.sh --fleet all backup
./lightsctl.sh --fleet venue-* restart
```

**Features:**
- Discovery and auto-registration
- Centralized configuration
- Synchronized updates
- Aggregate monitoring
- Batch operations

---

### 9. Enhanced Networking & Remote Access

**VPN Integration:**
- Tailscale/WireGuard setup
- Remote access from anywhere
- Secure by default
- One-command configuration
- Zero-trust networking
- Mesh network support

**Multi-Network Support:**
- Ethernet + WiFi dual-homing
- Automatic failover
- Network priority configuration
- Static IP per interface
- VLAN support for lighting traffic
- Dedicated lighting network isolation

**Network Diagnostics:**
- Real-time bandwidth monitoring
- Latency testing and alerts
- Connection quality metrics
- WiFi interference detection
- Network topology visualization
- Packet loss tracking

**Remote Access Features:**
- Secure tunneling
- Port forwarding automation
- Dynamic DNS integration
- Access logging and audit
- Session management
- Multi-user access control

---

### 10. Hardware Expansion & Integration

**Multiple DMX Interfaces:**
- Support for multiple ENTTEC devices
- Universe expansion (up to 4+ universes)
- Automatic detection and configuration
- Load balancing across interfaces
- Hot-swap support
- Redundancy and failover

**GPIO Integration:**
- Physical buttons and faders
- Hardware control surface (DIY or commercial)
- Emergency stop button
- Status LEDs (power, DMX, network)
- Rotary encoders
- LCD/OLED display integration
- Custom control panels

**Wireless DMX Configuration:**
- Transmitter setup wizard
- Channel mapping and patching
- Signal strength monitoring
- Frequency scanning
- Troubleshooting tools
- Multiple transmitter support
- Hybrid wired/wireless setups

**Additional Hardware:**
- ArtNet/sACN output support
- MIDI controller integration (Launchpad, APC, etc.)
- OSC (Open Sound Control) support
- USB DMX interface alternatives
- Ethernet DMX (eDMX) support
- Power monitoring and control

---

## Long-Term Vision (v3.0+ - 6-12 months)

### 11. Studio Ecosystem Integration

**Camera Integration:**
- Sync lighting with camera triggers
- Color temperature matching (match camera white balance)
- Exposure compensation (adjust lighting for camera settings)
- Automated adjustments based on camera metadata
- Multi-camera coordination
- Focus assist lighting
- Green screen optimization

**Video Production Workflow:**
- Timecode synchronization (SMPTE, LTC)
- OBS/vMix integration (scene switching triggers lighting)
- Automated scene changes based on video timeline
- Multi-camera support with lighting zones
- Recording indicator lights
- Tally light integration
- Live switching coordination

**Audio-Reactive Lighting:**
- Real-time audio input analysis
- BPM detection and tracking
- Beat-matched effects and strobing
- Music genre detection
- Frequency band isolation (bass, mids, highs)
- Volume-based intensity control
- Audio visualization modes
- MIDI clock sync

**Talent Monitoring:**
- Simple web view for talent to see upcoming lighting changes
- Countdown timers for scene changes
- Cue notifications
- Preview mode for talent approval

---

### 12. Advanced AI Features

**AI Scene Evolution:**
- Learn from user preferences over time
- Suggest improvements based on usage patterns
- Automatic optimization (energy efficiency, fixture wear)
- Style transfer (apply one scene's style to another)
- Personalized recommendations
- A/B testing for scene effectiveness

**Video Analysis:**
- Analyze video content frame-by-frame
- Suggest matching lighting for video mood
- Color palette extraction from video/images
- Mood and emotion detection
- Scene-by-scene lighting recommendations
- Automatic color grading sync

**Real-Time Adjustment:**
- AI watches camera output
- Suggests tweaks for better results
- Automatic correction (exposure, color cast)
- Quality monitoring and alerts
- Adaptive lighting based on content
- Predictive adjustments

**AI-Powered Features:**
- Natural language control ("make it warmer", "dim the backlight")
- Intelligent fixture grouping
- Automatic fixture addressing
- Conflict detection and resolution
- Energy optimization suggestions
- Maintenance predictions

---

### 13. Professional Features

**Show Management:**
- Complete show configurations (save entire setups)
- Visual timeline editor with drag-and-drop
- Cue lists with timing and transitions
- Rehearsal mode (run through without output)
- Show templates for common event types
- Multi-show management
- Show notes and documentation
- Backup and restore per show

**Client Portal:**
- Branded interface with custom logo/colors
- Preview and approval workflow
- Real-time collaboration tools
- Project management and milestones
- Client feedback and comments
- Revision history
- Asset sharing (photos, videos, workspaces)
- Invoice and quote generation

**Equipment Inventory:**
- Complete fixture tracking and database
- Maintenance schedules and reminders
- Usage analytics per fixture
- Rental management (check-in/out)
- Depreciation tracking
- Warranty management
- Spare parts inventory
- Equipment location tracking

**Usage Analytics:**
- Detailed runtime statistics
- Popular scenes and usage patterns
- System health trends over time
- Performance reports and benchmarks
- Energy consumption tracking
- Cost analysis
- ROI calculations
- Predictive maintenance alerts

**Business Tools:**
- Time tracking for projects
- Client database
- Quote and proposal generation
- Invoice integration
- Calendar and scheduling
- Resource allocation
- Profit/loss tracking

---

### 14. Platform & Extensibility

**Plugin System:**
- Third-party extensions and add-ons
- Custom integrations (write your own)
- Sandboxed execution environment
- Plugin marketplace
- API access for developers
- Webhook support for external triggers
- Event system for plugin communication
- Plugin SDK and documentation

**Cloud Sync (Optional):**
- Encrypted backup to cloud storage
- Multi-device synchronization
- Real-time collaboration features
- Version history and rollback
- Conflict resolution
- Selective sync (choose what to sync)
- Multiple cloud provider support
- Self-hosted cloud option

**Integration Hub:**
- **StreamDeck support** (custom buttons, scene triggers)
- **MIDI controller integration** (Launchpad, APC40, etc.)
- **Home automation** (Home Assistant, HomeKit, Alexa, Google Home)
- **REST API** (full programmatic control)
- **WebSocket API** (real-time bidirectional communication)
- **MQTT support** (IoT ecosystem integration)
- **OSC (Open Sound Control)** (audio software integration)
- **Zapier/IFTTT** (automation workflows)
- **Webhooks** (trigger external services)
- **GraphQL API** (flexible data queries)

**Developer Tools:**
- API documentation and examples
- SDK for multiple languages
- Testing and debugging tools
- Emulator for development
- CI/CD integration
- Performance profiling
- Log aggregation

---

## Community & Ecosystem

### Documentation
- Comprehensive video tutorials
- Interactive step-by-step guides
- Searchable troubleshooting database
- Best practices and design patterns
- Real-world case studies
- API reference documentation
- Architecture deep-dives
- Contributing guidelines

### Community Building
- Active Discord server with channels for support, showcase, development
- Forum/discussions for long-form conversations
- Showcase gallery with voting and comments
- Monthly lighting challenges and competitions
- Regional user meetups and conferences
- Mentorship program (experienced users help newcomers)
- Community moderators and ambassadors
- Annual Lights Pi conference

### Content Creation
- Regular blog posts (weekly/monthly)
- YouTube channel with tutorials and showcases
- Tutorial series for different skill levels
- Live streams (coding, Q&A, show setups)
- Podcast interviews with users and creators
- Newsletter with updates and tips
- Social media presence (Twitter, Instagram, TikTok)
- User-generated content program

### Partnerships
- Fixture manufacturers (official fixture profiles)
- DMX hardware vendors (tested compatibility)
- Content creator collaborations (YouTube, Twitch)
- Educational institutions (curriculum integration)
- Event production companies (professional use cases)
- Lighting designers (expert input)
- Software companies (integration partnerships)
- Maker spaces and hackerspaces

### Educational Programs
- Curriculum for schools and universities
- Workshop materials and lesson plans
- Student discounts and grants
- Teacher training programs
- Certification program
- Online courses and certifications

---

## Business Model (Optional)

### Free & Open Source (Core)
- All current features
- CLI toolkit
- Basic web interface
- Community support

### Premium Services (Optional)
- Hosted cloud backup ($5/month)
- Advanced analytics ($10/month)
- Priority support ($20/month)
- Custom development
- Consulting services

### Hardware Bundles
- Pre-configured Pi kits
- Tested fixture combinations
- Complete studio packages
- Rental-ready systems

### Marketplace Revenue
- Premium scene packs
- Professional templates
- Custom fixture profiles
- Show packages

---

## Technical Debt & Maintenance

### Code Quality
- Automated testing
- CI/CD pipeline
- Code coverage
- Performance benchmarks

### Documentation
- API documentation
- Architecture diagrams
- Contributing guide
- Code comments

### Security
- Regular audits
- Dependency updates
- Vulnerability scanning
- Penetration testing

### Performance
- Optimization passes
- Memory profiling
- Network efficiency
- Startup time reduction

---

## Success Metrics

### Adoption
- GitHub stars: 1,000+ (6 months), 5,000+ (12 months)
- Active installations: 500+ (6 months), 2,000+ (12 months)
- Discord members: 200+ (6 months), 1,000+ (12 months)

### Engagement
- Monthly active users
- Scene library downloads
- Showcase submissions
- Community contributions

### Quality
- Bug reports vs. resolutions
- Average response time
- User satisfaction score
- Feature request fulfillment

### Growth
- Website traffic
- Documentation views
- Video tutorial views
- Social media followers

---

## Release Schedule

### v1.1 (March 2026)
- ✅ Auto-load default workspace
- Enhanced documentation
- Bug fixes

### v1.2 (April 2026)
- Marketing website launch
- AI scene generation (beta)
- Scene library foundation

### v1.3 (May 2026)
- Scene library marketplace
- Health dashboard
- Community showcase

### v2.0 (July 2026)
- Multi-device management
- Advanced networking
- Hardware expansion
- Plugin system (alpha)

### v2.5 (October 2026)
- Studio ecosystem integration
- Advanced AI features
- Professional features

### v3.0 (January 2027)
- Platform maturity
- Enterprise features
- Full plugin ecosystem
- Cloud services (optional)

---

## Decision Framework

**When evaluating new features, consider:**

1. **User Impact:** Does this solve a real problem?
2. **Complexity:** Can we implement it cleanly?
3. **Maintenance:** Can we support it long-term?
4. **Community:** Will users contribute/extend it?
5. **Differentiation:** Does this set us apart?
6. **Resources:** Do we have time/skills/budget?

**Priority Matrix:**

| Impact | Complexity | Priority |
|--------|-----------|----------|
| High   | Low       | P0 (Do now) |
| High   | High      | P1 (Plan carefully) |
| Low    | Low       | P2 (Nice to have) |
| Low    | High      | P3 (Probably skip) |

---

## Creative & Experimental Ideas

### Motion-Triggered Lighting
- PIR sensor integration
- Camera-based motion detection
- Automatic scene activation
- Presence detection
- Occupancy-based energy saving

### Scheduled Scenes
- Time-based automation
- Sunrise/sunset sync
- Calendar integration
- Recurring schedules
- Holiday modes

### Color Palette Tools
- Import from photos/videos
- Match lighting to content
- Color theory suggestions
- Complementary color generation
- Brand color integration

### Virtual Lighting Designer
- 3D fixture visualization
- Virtual venue modeling
- Pre-visualization before setup
- Beam angle simulation
- Shadow and coverage analysis

### Lighting Presets Library
- Genre-specific presets (jazz, rock, classical)
- Mood-based presets (energetic, calm, dramatic)
- Time-of-day presets (morning, afternoon, evening)
- Weather-matched presets (sunny, cloudy, stormy)
- Season-specific presets

### Advanced Automation
- If-this-then-that rules
- Complex trigger chains
- State machines for shows
- Conditional scene activation
- External sensor integration

---

## Open Questions

1. **AI Provider:** Anthropic (Claude) vs OpenAI (GPT) vs local (Ollama)? Consider cost, privacy, and offline capability.
2. **Cloud Services:** Self-hosted only or offer optional cloud? Balance between privacy and convenience.
3. **Monetization:** Pure open source or hybrid model? How to sustain development long-term?
4. **Hardware:** Expand beyond Pi (x86, ARM alternatives, dedicated hardware)? Consider performance and cost.
5. **Mobile App:** Native app or PWA sufficient? Current web interface works well, but native could offer more.
6. **Enterprise:** Target enterprise market or stay prosumer? Different needs and support requirements.
7. **Certification:** Offer professional certification program? Could drive adoption in education and professional markets.
8. **Marketplace:** Centralized or decentralized? How to handle quality control and payments?

---

## Contributing

This roadmap is a living document. Community input is welcome!

**How to contribute:**
- Open GitHub issues for feature requests
- Join Discord to discuss ideas
- Submit PRs for documentation
- Share your use cases
- Vote on feature priorities

**Roadmap updates:**
- Reviewed monthly
- Community feedback incorporated
- Priorities adjusted based on usage
- Transparent decision-making

---

## Conclusion

Lights Pi started as a simple Raspberry Pi lighting controller. The vision is to become the go-to open-source platform for lighting control - accessible, powerful, and community-driven.

**Core Principles:**
- Open source first
- User-focused design
- Community-driven development
- Professional quality
- Accessible to all skill levels

**Next Steps:**
1. ✅ Implement auto-load workspace
2. Launch marketing website
3. Build AI scene generation
4. Grow community
5. Iterate based on feedback

Let's build something amazing together.
