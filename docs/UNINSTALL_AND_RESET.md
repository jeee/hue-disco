# Uninstall and reset

Use `uninstall.yml` for a clean reinstall.

Soft reset keeps diyHue bridge state and removes Hue Disco code and services:

```bash
ansible-playbook -i inventory.ini uninstall.yml
```

Full reset removes Hue Disco and diyHue state for a truly fresh install:

```bash
ansible-playbook -i inventory.ini uninstall.yml -e reset_mode=full
```
