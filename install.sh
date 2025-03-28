#!/bin/bash

# install TopGen
# (glsomlo@cert.org, May 2016)

/bin/echo "Installing using the following filesystem locations:"
/bin/echo "NAME         =${NAME:=topgen};"
/bin/echo "BUILDROOT    =${BUILDROOT:=};"
/bin/echo "UNITDIR      =${UNITDIR:=/usr/lib/systemd/system};"
/bin/echo "SYSCONFDIR   =${SYSCONFDIR:=/etc};"
/bin/echo "LOCALSTATEDIR=${LOCALSTATEDIR:=/var};"
/bin/echo "SBINDIR      =${SBINDIR:=/sbin};"
/bin/echo "MANDIR       =${MANDIR:=/usr/share/man};"
/bin/echo "TEMPLATESDIR =${TEMPLATESDIR:=/templates};"

/usr/bin/install -d $BUILDROOT/$UNITDIR
/usr/bin/install -d $BUILDROOT/$SBINDIR
/usr/bin/install -d $BUILDROOT/$MANDIR/man8
/usr/bin/install -d $BUILDROOT/$SYSCONFDIR/nginx/conf.d
/usr/bin/install -d $BUILDROOT/$SYSCONFDIR/$NAME
/usr/bin/install -d $BUILDROOT/$SYSCONFDIR/$NAME/$TEMPLATESDIR/
/usr/bin/install -d $BUILDROOT/$SYSCONFDIR/$NAME/$TEMPLATESDIR/topgen-scrape
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/custom_vhosts
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/custom_vhosts/www.msftncsi.com
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/etc/postfix
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/vhosts
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/certs
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/named
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/vmail
/usr/bin/install -d $BUILDROOT/$LOCALSTATEDIR/named

if [ -f $BUILDROOT/$UNITDIR/topgen-$svc ]; then
	/bin/ln -s $LOCALSTATEDIR/lib/$NAME/etc/nginx.conf \
			$BUILDROOT/$SYSCONFDIR/nginx/conf.d/topgen.conf
fi
# symlink to standard services, then amend via drop-in override configurations:
for i in systemd/topgen-*.service.d; do
	tmp=${i##*topgen-}
	svc=${tmp%%.d} 
	if [ -f $BUILDROOT/$UNITDIR/topgen-$svc ]; then
		/bin/ln -s $svc $BUILDROOT/$UNITDIR/topgen-$svc
	fi
done
cp -r systemd/* $BUILDROOT/$UNITDIR
/usr/bin/install -m 0755 -t $BUILDROOT/$SBINDIR sbin/*
/usr/bin/install -m 0644 -t $BUILDROOT/$MANDIR/man8 man/*
/usr/bin/install -m 0644 -t $BUILDROOT/$SYSCONFDIR/$NAME etc/*
/usr/bin/install -m 0644 -t $BUILDROOT/$SYSCONFDIR/$NAME/$TEMPLATESDIR/topgen-scrape templates/topgen-scrape/*
/usr/bin/install -m 0644 -t $BUILDROOT/$LOCALSTATEDIR/lib/$NAME/custom_vhosts/www.msftncsi.com custom_vhosts/www.msftncsi.com/*
