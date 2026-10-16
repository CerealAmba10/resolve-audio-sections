"""One executable serves as installer and installed background helper."""
import sys

if __name__ == '__main__':
    if '--worker' in sys.argv:
        from worker import main
        main()
    elif '--install-only' in sys.argv:
        from setup_plugin import install
        install(single_file=True)
    else:
        from setup_plugin import main
        main(single_file=True)
