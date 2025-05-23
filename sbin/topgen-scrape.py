#!/bin/python3

from urllib.parse import urlparse
import logging
import asyncio
import glob
import re
import shutil
import os
from string import Template
import tempfile
import atexit
import enlighten
import argparse
import resource
import socket

# How deep will wget go, setting this to anything more than 1 will increase scrape time by a LOT, but will result in more complete sites
wget_depth = 1

TOPGEN_VARLIB = os.path.realpath("/var/lib/topgen")
TOPGEN_ETC = os.path.realpath("/etc/topgen")
TOPGEN_VHOSTS = os.path.join(TOPGEN_VARLIB, "vhosts")
TOPGEN_VARETC = os.path.join(TOPGEN_VARLIB, "etc")
TOPGEN_CERTS = os.path.join(TOPGEN_VARLIB, "certs")
TOPGEN_TEMPLATES = os.path.join(TOPGEN_VARLIB, "templates/topgen-scrape")

TOPGEN_ORIG = os.path.join(TOPGEN_ETC, "scrape_sites.txt")
TOPGEN_CUSTOM_VHOSTS = os.path.join(TOPGEN_ETC, "custom_vhosts")

# topgen.info vhost directory:
TOPGEN_SITE = os.path.join(TOPGEN_VHOSTS, "topgen.info")

# The maximum number of open file descriptors, if you get an error about too many open files, increase this number
# 8192 is more than enough for ~500 sites
TOPGEN_NOFILE = 8192

# up limits so topgen-scrape.py won't run out of file descriptors:
resource.setrlimit(
    resource.RLIMIT_NOFILE,
    (TOPGEN_NOFILE, TOPGEN_NOFILE))

# Ensure directories exist
os.makedirs(TOPGEN_VHOSTS, exist_ok=True)
os.makedirs(TOPGEN_CERTS, exist_ok=True)
os.makedirs(TOPGEN_VARETC, exist_ok=True)

#CA
TMP_CA_DIR = None
CA_CONF_PATH = None

# enlighten progress
manager = enlighten.get_manager()
BAR_FMT = '{desc}:{desc_pad}{percentage:3.0f}% |{bar}| {count:{len_total}d}/{total:d} [Elapsed: {elapsed}]'


# Modify the logging configuration at the top
logging.basicConfig(
#    filename='topgen-scrape.log',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s: %(message)s',
)

# Log both to console and file
logger = logging.getLogger("enlighten")
#logger.addHandler(logging.FileHandler('topgen-scrape.log'))
logger.addHandler(logging.StreamHandler())

# Helper functions
def format_elapsed_time(seconds):
    """Format elapsed time showing only non-zero hours and minutes"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    elif minutes > 0:
        return f"{minutes}m {secs:02d}s"
    else:
        return f"{secs}s"

async def update_progress(tasks, pbar):
    while not all(task.done() for task in tasks):
        completed = sum(task.done() for task in tasks)
        pbar.count = completed
        await asyncio.sleep(1)
# Big Boy Functions

async def download_websites():
    """Download all websites from TOPGEN_ORIG"""
    tasks = []
    # Create task list
    for url in open(TOPGEN_ORIG):
        if url.startswith('#'):
            continue
        task = asyncio.create_task(download_website(url))
        tasks.append(task)
    
    # Create progress bar
    pbar = manager.counter(total=len(tasks),desc='Scraping Websites',bar_format=BAR_FMT)
    await asyncio.sleep(0.1)  # Wait for progress bar to initialize
    # Update progress bar every second
    
    # Run progress updater and tasks
    update_task = asyncio.create_task(update_progress(tasks, pbar))
    await asyncio.gather(*tasks)
    await update_task

    # Final update and close
    pbar.count = len(tasks)
    pbar.close()

async def download_website(url):
    url = url.strip()
    if not url:
        raise ValueError("URL is empty")
    hostname = urlparse(url).hostname
    pbar = manager.counter(desc='    Scraping %s' % hostname, autorefresh=True, leave=False, counter_format='{desc}:{desc_pad}[Elapsed: {elapsed}]')
    try:
        proc = await asyncio.create_subprocess_shell(
            f"/usr/bin/wget -v --page-requisites --recursive --adjust-extension --span-hosts -N --convert-file-only --no-check-certificate -e robots=off --random-wait -t 2 -U 'Mozilla/5.0 (X11)' -P {TOPGEN_VHOSTS} -l {wget_depth} {url}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        
        # Process stdout and stderr streams simultaneously
        async def read_stream(stream):
            while True:
                line = await stream.readline()
                if not line:
                    break
                logger.debug(f'[{hostname}] {line.decode().strip()}')
        
        # Create tasks for reading both streams
        stdout_task = asyncio.create_task(read_stream(proc.stdout))
        stderr_task = asyncio.create_task(read_stream(proc.stderr))
        
        # Wait for wget to complete and streams to be processed
        await proc.wait()
        await stdout_task
        await stderr_task
        
        
        if proc.returncode != 0:
            logger.error(f'{hostname}: wget returned non-zero exit code {proc.returncode}')
        
        logger.info(f'✓ {hostname} ({format_elapsed_time(pbar.elapsed)})')

    except Exception as e:
        logger.error(f'Failed {hostname} after {format_elapsed_time(pbar.elapsed)}: {str(e)}')
    finally:
        pbar.close()

async def handle_custom_vhosts():
    """Process custom vhosts from TOPGEN_VARETC/custom_vhosts directory"""
    if not os.path.exists(TOPGEN_CUSTOM_VHOSTS):
        logger.debug("No custom vhosts directory found")
        return

    vhosts = list(os.scandir(TOPGEN_CUSTOM_VHOSTS))
    with manager.counter(total=len(vhosts), desc='Handling Custom vHosts', bar_format=BAR_FMT) as pbar:
        for vhost in vhosts:
            if vhost.is_dir():
                vhost_name = vhost.name
                vhost_content = os.path.join(TOPGEN_CUSTOM_VHOSTS, vhost_name)
                vhost_destination = os.path.join(TOPGEN_VHOSTS, vhost_name)

                # Copy website content to vhosts directory
                if os.path.exists(vhost_destination):
                    shutil.rmtree(vhost_destination)
                shutil.copytree(vhost_content, vhost_destination)
                logger.debug(f"Copied custom vhost: {vhost_name}")
            pbar.update(1)

async def cleanup_vhosts():
    """Remove IP-only vhosts and vhosts with port numbers"""
    vhosts = list(glob.glob(f"{TOPGEN_VHOSTS}/*"))
    with manager.counter(total=len(vhosts), desc='Cleaning vhosts', bar_format=BAR_FMT) as pbar:
        for vhost in vhosts:
            vhost_name = os.path.basename(vhost)
            if re.match(r'^[\d.]+$', vhost_name) or ':' in vhost_name:
                logger.debug(f"Cleaning up: Removing {vhost_name}")
                shutil.rmtree(vhost, ignore_errors=True)
            pbar.update(1)

async def curate_vhosts():
    """Handle www.example.org/index.html issue"""
    vhosts = list(glob.glob(f"{TOPGEN_VHOSTS}/*"))
    with manager.counter(total=len(vhosts), desc='Curating vhosts', bar_format=BAR_FMT) as pbar:
        for vhost in vhosts:
            vhost_base = os.path.basename(vhost)
            
            if not os.path.isdir(vhost):
                pbar.update(1)
                continue
                
            num_files = len(os.listdir(vhost))
            
            if (num_files == 1 and 
                os.path.isfile(os.path.join(vhost, "index.html"))):
                
                www_vhost = os.path.join(TOPGEN_VHOSTS, f"www.{vhost_base}")
                if (os.path.isdir(www_vhost) and 
                    not os.path.isfile(os.path.join(www_vhost, "index.html"))):
                    
                    src = os.path.join(vhost, "index.html")
                    dst = os.path.join(www_vhost, "index.html")
                    shutil.copy2(src, dst)
                    logger.info(f"Curated: {src} -> {dst}")
            pbar.update(1)

async def generate_landing_page():
    """Generate the topgen.info landing page"""
    with manager.counter(total=1, desc='Generating topgen.info', bar_format=BAR_FMT) as pbar:
        # Create topgen.info directory
        os.makedirs(TOPGEN_SITE, exist_ok=True)

        # Get list of vhosts (excluding topgen.info itself)
        vhosts = [os.path.basename(v) for v in glob.glob(f"{TOPGEN_VHOSTS}/*") 
                if not v.endswith('topgen.info')]
        html_content = ""
        # Add vhost entries
        for vhost in sorted(vhosts):
            html_content += f'      <li><a href="//{vhost}">{vhost}</a>\n'
        
        # Write file from Template
        index_path = os.path.join(TOPGEN_SITE, "index.html")
        with open(index_path, 'w') as f:
            with open (os.path.join(TOPGEN_TEMPLATES, "topgen.info"), 'r') as template:
                template_source = Template(template.read())
                template_result = template_source.substitute(vhosts=html_content)
            f.write(template_result)
        
        logger.debug(f"Generated landing page with {len(vhosts)} vhosts")
        pbar.update(1)

async def generate_CA():
    """Generate SSL certificates for TopGen"""
    with manager.counter(total=1, desc='Generating CA', bar_format=BAR_FMT) as pbar:
        # Check if CA certificates already exist
        logger.debug("Checking for existing CA certificates")
        ca_key = os.path.join(TOPGEN_VARETC, "topgen_ca.key")
        ca_cert = os.path.join(TOPGEN_VARETC, "topgen_ca.cer")
        
        # Generate CA if not exists
        if not (os.path.exists(ca_key) and os.path.exists(ca_cert)):
            logger.debug("Not Found, Generating CA")
            proc = await asyncio.create_subprocess_exec(
                'openssl', 'req', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', ca_key,
                '-days', '7300', '-x509',
                '-out', ca_cert,
                '-subj', '/C=US/ST=PA/L=Pgh/O=CMU/OU=CERT/CN=topgen_ca',
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()

        # Copy CA cert to topgen.info site
        os.makedirs(TOPGEN_SITE, exist_ok=True)
        shutil.copy2(ca_cert, TOPGEN_SITE)
        logger.debug("Copied CA cert to topgen.info site")
        # Generate vhost key if not exists
        vh_key = os.path.join(TOPGEN_VARETC, "topgen_vh.key")
        if not os.path.exists(vh_key):
            logger.debug("Generating vhost key")

            proc = await asyncio.create_subprocess_exec(
                'openssl', 'genrsa',
                '-out', vh_key,
                '2048',
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
        
        # Create temporary CA directory structure
        logger.debug("Creating temporary CA directory")
        tmp_ca_dir = tempfile.mkdtemp(prefix='TopGenCA.')
        
        # Create serial and index files
        logger.debug("Creating serial file")
        with open(os.path.join(tmp_ca_dir, "serial"), 'w') as f:
            f.write("000a")

        logger.debug("Creating index file")
        with open(os.path.join(tmp_ca_dir, "index"), 'w') as f:

            pass  # Create empty index file
    
        # Write CA configuration
        logger.debug("Creating CA configuration file")
        ca_conf_path = os.path.join(tmp_ca_dir, "ca.conf")

        # Create Dict of CA configuration values
        logger.debug("Creating CA configuration dictionary")
        ca_dict = {
            'tmp_ca_dir': tmp_ca_dir,
            'ca_cert': ca_cert,
            'ca_key': ca_key
        }

        with open(ca_conf_path, 'w') as f:
            with open (os.path.join(TOPGEN_TEMPLATES, "CertificateAuthority.conf"), 'r') as template:
                template_source = Template(template.read())
                template_result = template_source.substitute(ca_dict)
            f.write(template_result)
        
        # Store tmp_ca_dir path for cleanup
        global TMP_CA_DIR
        TMP_CA_DIR = tmp_ca_dir
        global CA_CONF_PATH
        CA_CONF_PATH = ca_conf_path
        
        logger.debug(f"Certificate generation complete. Temporary CA dir: {tmp_ca_dir}")
        pbar.update(1)

async def generate_vhost_certificates():
    """Generate certificates and nginx configuration for all vhosts"""
    global CA_CONF_PATH

    vhosts = list(glob.glob(f"{TOPGEN_VHOSTS}/*"))
    # Get CSR configuration
    vh_template = open(os.path.join(TOPGEN_TEMPLATES, "vHost_CSR.conf"), 'r')

    with manager.counter(total=len(vhosts), desc='Generate vHost Certificates', bar_format=BAR_FMT) as pbar:
        for vhost in vhosts:
            logger.debug(f"[{vhost}] Generating Certificate")
            vhost_base = os.path.basename(vhost)
            cert_path = os.path.join(TOPGEN_CERTS, f"{vhost_base}.cer")

            # Generate CSR configuration
            template_source = Template(vh_template.read())
            vh_conf = template_source.substitute(vhost_base=vhost_base)
        
            # Generate certificate
            proc = await asyncio.create_subprocess_exec(
                'openssl', 'ca', '-batch', '-notext',
                '-config', CA_CONF_PATH,
                '-out', cert_path,
                '-in', '-',
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )

            # Generate CSR and pipe to CA
            csr_cmd = [
                'openssl', 'req', '-new',
                '-key', os.path.join(TOPGEN_VARETC, "topgen_vh.key"),
                '-subj', '/C=US/ST=PA/L=Pgh/O=CMU/OU=CERT/CN=topgen_vh',
                '-addext', f'subjectAltName = DNS:{vhost_base}'
            ]
            logger.debug(f"[{vhost}] Sending Certificate Signing Request")
            csr_proc = await asyncio.create_subprocess_exec(
                *csr_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
                
            csr, _ = await csr_proc.communicate(vh_conf.encode())
            await proc.communicate(csr)

            pbar.update(1)
            logger.debug(f"[{vhost}] Wrote certificate to {cert_path}")

async def generate_hosts_nginx():
    vhosts = list(glob.glob(f"{TOPGEN_VHOSTS}/*"))
    tasks = []

    # Create task list
    for vhost in vhosts:
        task = asyncio.create_task(generate_vhost_hosts_nginx(vhost))
        tasks.append(task)

    # Create progress bar
    pbar = manager.counter(total=len(tasks), desc='Generating hosts.nginx', autorefresh=True, bar_format=BAR_FMT)
    await asyncio.sleep(0.1)  # Wait for progress bar to initialize

    # Run progress updater and tasks
    update_task = asyncio.create_task(update_progress(tasks, pbar))
    await asyncio.gather(*tasks)
    await update_task

    # Final update and close
    pbar.count = len(tasks)
    pbar.close()

async def generate_vhost_hosts_nginx(vhost):
    vhost_base = os.path.basename(vhost)

    # Resolve IP address
    try:
        # Using socket to resolve hostname
        logger.debug(f"[{vhost}] Gathering IP Address from external DNS")
        vhost_ip = socket.gethostbyname(vhost_base)
    except socket.gaierror:
        # Use fallback IP for unresolvable hosts
        vhost_ip = "1.0.0.0"
        logger.warning(f"[{vhost}] Unable to resolve IP address, using fallback IP {vhost_ip}")

    # Append to hosts.nginx file
    with open(os.path.join(TOPGEN_VARETC, "hosts.nginx"), 'a') as f:
        f.write(f"{vhost_ip} {vhost_base}\n")
    logger.debug(f"[{vhost}] Wrote {vhost_ip} {vhost_base} to hosts.nginx")

async def generate_nginx_conf():
    vhosts = list(glob.glob(f"{TOPGEN_VHOSTS}/*"))

    # Remove old nginx.conf if exists
    nginx_conf = os.path.join(TOPGEN_VARETC, "nginx.conf")
    if os.path.exists(nginx_conf):
        os.remove(nginx_conf)
    
    with manager.counter(total=len(vhosts), desc='Generating nginx.conf', bar_format=BAR_FMT) as pbar:
        # Generate base nginx.conf
        try:
            with open(os.path.join(TOPGEN_VARETC, "nginx.conf"), 'w') as f:
                with open (os.path.join(TOPGEN_TEMPLATES, "nginx.conf_base"), 'r') as template:
                    template_source = Template(template.read())
                    template_result = template_source.substitute(TOPGEN_VARETC=TOPGEN_VARETC)
                f.write(template_result)
                f.flush()
                logger.debug("Writing base for nginx.conf")
        except Exception as e:
            logger.error(f'Failed writing base for nginx.conf: {str(e)}')

        # Get nginx block template
        try:
            with open(os.path.join(TOPGEN_TEMPLATES, "nginx.conf_vhost"), 'r') as template:
                template_source = Template(template.read())
                for vhost in vhosts:
                    vhost_base = os.path.basename(vhost)
                    cert_path = os.path.join(TOPGEN_CERTS, f"{vhost_base}.cer")
                    # Append to nginx.conf file
                    with open(os.path.join(TOPGEN_VARETC, "nginx.conf"), 'a') as f:
                        nginx_block = template_source.substitute(cert_path=cert_path, vhost_base=vhost_base, vhost=vhost)
                        f.write(nginx_block)
                        logger.debug(f"[{vhost_base}] Wrote vhost block to nginx.conf")
                    pbar.update(1)
        except Exception as e:
            logger.error(f'[{vhost_base}] Failed writing vhost for nginx.conf: {str(e)}')

        logger.debug(f"Finished writing nginx.conf for {len(vhosts)} vhosts")

async def main():
    global TOPGEN_ORIG
    global TOPGEN_VARLIB

    parser = argparse.ArgumentParser(description="Recursively scrape, clean, curate a given list of Web sites. Additionally, issue certificates signed with a self-signed TopGen CA (which is in turn also generated, if necessary). Generate a drop-in config file for the nginx HTTP server, and a hosts file containing <ip_addr fqdn> entries for each scraped vhost.", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-s", "--sites", help=f"file containing space or newline separated sites to be scraped for static content; lines beginning with '#' are ignored;\n(default: {TOPGEN_ORIG})", default=TOPGEN_ORIG)
    parser.add_argument("-t", "--target-dir", help=f"directory where all results (scraped content, list of vhosts, certificates, configuration files, etc. are stored;\n(default: {TOPGEN_VARLIB})", default=TOPGEN_VARLIB)
    # Set Environment to either 'Development' or 'Production'
    # dev will always overwrite all filed while prod will only write if the file does not exist
    parser.add_argument("-e", "--environment", help="environment in which to run the script; 'Development' will overwrite all files, 'Production' will only write files that do not exist;\\n(default: Production)", default="Production")
    parser.add_argument("-d", "--skip-scrape", help="Skip the scraping of websites, for if you want to quickly add new vhosts.", action='store_false')
    parser.add_argument("-n", "--skip-hosts", help="Skip generating of the hosts.nginx file", action='store_false')
    args = parser.parse_args()
    TOPGEN_ORIG = args.sites
    TOPGEN_VARLIB = args.target_dir
    ENVIRONMENT = args.environment

    status = manager.status_bar(status_format=u'Topgen-Scrape - {ENVIRONMENT}{fill}{stage}{fill}{elapsed}',
        color='bold_underline_bright_white_on_lightslategray',
        justify=enlighten.Justify.CENTER, autorefresh=True, min_delta=0.5, stage='Initializing', ENVIRONMENT=ENVIRONMENT)
    
    if ENVIRONMENT == "Development" or ENVIRONMENT == "Production" and len(os.listdir(TOPGEN_VHOSTS)) == 0:
        status.update(stage="Creating vHosts")
        if args.skip_scrape:
            await download_websites()
        else:
            manager.counter(desc='Skipped Scraping Sites').close()
        await handle_custom_vhosts()
        await cleanup_vhosts()
        await curate_vhosts()
        await generate_landing_page()
    else:
        logger.debug("Skipping vHost creation")

    if ENVIRONMENT == "Development" or ENVIRONMENT == "Production" and len(os.listdir(TOPGEN_CERTS)) == 0:
        status.update(stage="Generating Certificates")
        await generate_CA()
        await generate_vhost_certificates()
    else:
        logger.debug("Skipping certificate generation")
    
    if ENVIRONMENT == "Development" or ENVIRONMENT == "Production" and not os.path.exists(os.path.join(TOPGEN_ETC, "hosts.nginx")) and os.path.exists(os.path.join(TOPGEN_ETC, "nginx.conf")):
        status.update(stage="Generating Nginx config files")
        if ENVIRONMENT == "Development" or ENVIRONMENT == "Production" and not len(os.path.exists(os.path.join(TOPGEN_ETC, "hosts.nginx"))):
            if args.skip_hosts:
                await generate_hosts_nginx()
            else:
                manager.counter(desc='Skipped generating hosts.nginx').close()
        else:
            logger.debug("Skipping hosts.nginx generation")
        if ENVIRONMENT == "Development" or ENVIRONMENT == "Production" and not os.path.exists(os.path.join(TOPGEN_ETC, "nginx.conf")):
            await generate_nginx_conf()
        else: 
            logger.debug("Skipping nginx.conf generation")
    else:
        logger.debug("Skipping Nginx config generation")
    
    status.update(stage="Finished")
    # Add cleanup at end of script
    atexit.register(lambda: shutil.rmtree(TMP_CA_DIR, ignore_errors=True))
    

asyncio.run(main())
